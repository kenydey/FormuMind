"""End-to-end orchestration: research -> recommend -> DOE -> simulate -> optimize.

This is the glue layer. It maps domain requirements onto the service adapters
and the DOE/optimizer engines, keeping a single source of truth for which
formulation levers are tuned per product family.
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

from ..config import get_settings
from ..domain import doe as doe_engine
from ..domain import knowledge
from ..domain.chemistry import validate_formulation
from ..domain.schemas import (
    DOEFactor,
    DOEPlan,
    Formulation,
    ObjectiveSpec,
    OptimizationResult,
    ProductDomain,
    Requirement,
    ResearchResult,
)
from ..services import literature, llm, predictor
from ..services.optimizer import Factor, build_optimizer
from ..services.rag import TfidfStore
from . import reconstruct

# Per-domain optimization levers: (ingredient name, role-fallback, low%, high%)
# and the objective metric to maximise.
LEVERS: dict[ProductDomain, list[tuple[str, float, float]]] = {
    ProductDomain.anticorrosion_coating: [
        ("Zinc phosphate", 2.0, 14.0),
        ("Bisphenol-A epoxy (DGEBA)", 28.0, 48.0),
        ("Polyamide hardener", 8.0, 22.0),
    ],
    ProductDomain.degreaser: [
        ("Nonionic surfactant (C12-14 EO7)", 2.0, 12.0),
        ("Sodium metasilicate", 2.0, 14.0),
    ],
    ProductDomain.surface_treatment: [
        ("Phosphoric acid", 3.0, 14.0),
        ("Manganese dihydrogen phosphate", 1.0, 8.0),
    ],
}

OBJECTIVE: dict[ProductDomain, str] = {
    ProductDomain.anticorrosion_coating: "salt_spray_hours",
    ProductDomain.degreaser: "cleaning_efficiency",
    ProductDomain.surface_treatment: "salt_spray_hours",
}

_DEFAULT_OBJECTIVES: dict[ProductDomain, list[ObjectiveSpec]] = {
    ProductDomain.anticorrosion_coating: [
        ObjectiveSpec(metric="salt_spray_hours", weight=0.5, direction="maximize"),
        ObjectiveSpec(metric="cost_cny_per_kg", weight=0.25, direction="minimize"),
        ObjectiveSpec(metric="sustainability_idx", weight=0.25, direction="maximize"),
    ],
    ProductDomain.degreaser: [
        ObjectiveSpec(metric="cleaning_efficiency", weight=0.5, direction="maximize"),
        ObjectiveSpec(metric="cost_cny_per_kg", weight=0.3, direction="minimize"),
        ObjectiveSpec(metric="voc_gpl", weight=0.2, direction="minimize"),
    ],
    ProductDomain.surface_treatment: [
        ObjectiveSpec(metric="salt_spray_hours", weight=0.5, direction="maximize"),
        ObjectiveSpec(metric="coating_weight_gsm", weight=0.2, direction="maximize"),
        ObjectiveSpec(metric="cost_cny_per_kg", weight=0.3, direction="minimize"),
    ],
}


def default_objectives(domain: ProductDomain) -> list[ObjectiveSpec]:
    return _DEFAULT_OBJECTIVES[domain]


def process_for(req: Requirement) -> dict:
    """Process parameters used as predictor features (cure temp for thermosets)."""
    if req.domain == ProductDomain.anticorrosion_coating:
        return {"cure_temperature_c": req.cure_temperature_c}
    return {}


def _score_and_validate(
    form: Formulation,
    process: dict | None = None,
    req: Requirement | None = None,
) -> Formulation:
    form.predicted, form.predicted_std = predictor.predict_full(form, process)
    voc_limit = req.voc_limit_gpl if req else None
    form.warnings = validate_formulation(form, voc_limit_gpl=voc_limit)
    form.score = float(form.predicted.get(OBJECTIVE[form.domain], 0.0))
    return form


def run_research(req: Requirement, pre_sources: list | None = None) -> ResearchResult:
    """Run the research pipeline.

    If ``pre_sources`` is provided (non-empty list of Evidence), internal
    patent/literature retrieval is skipped and the supplied evidence is used
    directly — this allows the /api/search and /api/ingest endpoints to feed
    evidence into the pipeline without a redundant network round-trip.
    """
    from ..domain.schemas import Evidence as _Evidence
    if pre_sources:
        evidence: list[_Evidence] = list(pre_sources)
    else:
        evidence = literature.search(req)
    store = TfidfStore()
    store.ingest(evidence)
    grounded = store.query(req.headline(), k=min(5, len(evidence))) or evidence

    process = process_for(req)
    recommended = [_score_and_validate(f, process, req) for f in knowledge.variant_formulations(req, n=3)]
    recommended.sort(key=lambda f: (f.score or 0.0), reverse=True)

    mechanism, chat = llm.synthesize_research(req, grounded, recommended)
    return ResearchResult(
        requirement_headline=req.headline(),
        evidence=grounded,
        mechanism=mechanism,
        recommended=recommended,
        chat_markdown=chat,
    )


def _levers_for(form: Formulation) -> list[tuple[str, float, float]]:
    return LEVERS[form.domain]


# In-memory DOE plan cache so generated plans can be exported / round-tripped
# via /api/doe/{plan_id}/export. Bounded to avoid unbounded growth in long runs.
_PLAN_CACHE: dict[str, DOEPlan] = {}
_PLAN_CACHE_LOCK = threading.Lock()
_PLAN_CACHE_MAX = 64


def _cache_plan(plan: DOEPlan) -> None:
    with _PLAN_CACHE_LOCK:
        _PLAN_CACHE[plan.plan_id] = plan
        if len(_PLAN_CACHE) > _PLAN_CACHE_MAX:
            # Drop the oldest inserted plan (dicts preserve insertion order).
            oldest = next(iter(_PLAN_CACHE))
            _PLAN_CACHE.pop(oldest, None)


def get_cached_plan(plan_id: str) -> DOEPlan | None:
    with _PLAN_CACHE_LOCK:
        return _PLAN_CACHE.get(plan_id)


def build_doe(req: Requirement, design: str = "full_factorial") -> DOEPlan:
    base = knowledge.baseline_formulation(req)
    factors = [DOEFactor(name=name, low=low, high=high, unit="wt%") for name, low, high in _levers_for(base)]
    # Cure temperature is a meaningful process factor for thermosets.
    if req.domain == ProductDomain.anticorrosion_coating:
        factors.append(DOEFactor(name="cure_temperature_c", low=max(20.0, req.cure_temperature_c - 30), high=req.cure_temperature_c, unit="C"))
    plan = doe_engine.build_plan(factors, design=design)
    plan.plan_id = uuid.uuid4().hex
    plan.domain = req.domain
    _cache_plan(plan)
    return plan


def _apply_levers(req: Requirement, values: dict[str, float]) -> Formulation:
    """Build a fresh formulation with lever ingredient percentages overridden."""
    return reconstruct.formulation_from_factors(req.domain, values)


def run_optimization(
    req: Requirement,
    iterations: int | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> OptimizationResult:
    settings = get_settings()
    iterations = iterations or settings.optimize_iterations
    base = knowledge.baseline_formulation(req)
    levers = _levers_for(base)
    factors = [Factor(name=n, low=lo, high=hi) for n, lo, hi in levers]
    opt = build_optimizer(factors=factors, seed=42)
    objective = OBJECTIVE[req.domain]
    objectives = req.objectives or default_objectives(req.domain)
    process = process_for(req)
    history: list[float] = []
    best_so_far = float("-inf")
    # Dynamic bounds for multi-objective normalisation: seeded from the baseline.
    bounds: dict[str, tuple[float, float]] = {}
    base_props = predictor.predict(base, process)
    for metric, val in base_props.items():
        bounds[metric] = (val * 0.5, val * 1.5) if val > 0 else (0.0, 1.0)

    for it in range(iterations):
        x = opt.suggest()
        values = {f.name: v for f, v in zip(factors, x)}
        form = _apply_levers(req, values)
        props = predictor.predict(form, process)
        # Expand running bounds.
        for metric, val in props.items():
            lo, hi = bounds.get(metric, (val, val))
            bounds[metric] = (min(lo, val), max(hi, val))
        score = predictor.multi_objective_score(form, objectives, process, bounds)
        opt.observe(x, score)
        best_so_far = max(best_so_far, score)
        history.append(round(best_so_far, 3))
        if progress_cb:
            progress_cb((it + 1) / iterations, f"iter {it + 1}/{iterations}: best={best_so_far:.3f}")

    top: list[Formulation] = []
    for x, score in opt.ranked(settings.top_n_formulas):
        values = {f.name: v for f, v in zip(factors, x)}
        form = _score_and_validate(_apply_levers(req, values), process, req)
        form.name = f"Optimized {req.domain.value} (score {score:.3f})"
        top.append(form)
    return OptimizationResult(
        iterations=iterations,
        objective=objective,
        objectives=objectives,
        history=history,
        top_formulations=top,
        engine=getattr(opt, "engine", "numpy-ucb"),
    )
