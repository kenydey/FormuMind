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
from ..domain.chemistry import full_safety_check, validate_formulation
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
from ..services import llm, predictor
from ..services.optimizer import Factor, build_optimizer
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
    if req.domain == ProductDomain.anticorrosion_coating and req.cure_temperature_c is not None:
        return {"cure_temperature_c": req.cure_temperature_c}
    return {}


def _score_and_validate(
    form: Formulation,
    process: dict | None = None,
    req: Requirement | None = None,
) -> Formulation:
    from ..domain.project_spec import normalize_requirement, primary_objective

    req = normalize_requirement(req) if req else None
    form.predicted, form.predicted_std = predictor.predict_full(form, process, req=req)
    voc_limit = req.voc_limit_gpl if req else None
    form.warnings = validate_formulation(form, voc_limit_gpl=voc_limit)
    voc_gpl = form.predicted.get("voc_gpl")
    form.warnings.extend(full_safety_check(form, voc_gpl=voc_gpl, voc_limit_gpl=voc_limit))
    if req and req.objectives:
        if len(req.objectives) == 1:
            metric = req.objectives[0].metric
            form.score = float(form.predicted.get(metric, 0.0))
        else:
            objectives = req.objectives
            bounds: dict[str, tuple[float, float]] = {}
            for metric, val in form.predicted.items():
                bounds[metric] = (val * 0.5, val * 1.5) if val > 0 else (0.0, 1.0)
            form.score = float(predictor.multi_objective_score(form, objectives, process, bounds))
    else:
        metric = primary_objective(req) if req else OBJECTIVE[form.domain]
        form.score = float(form.predicted.get(metric, 0.0))
    return form


def _evidence_matches_type(evidence, source_type: str) -> bool:
    src = (evidence.source or "").lower()
    ident = (evidence.identifier or "").lower()
    if source_type == "patents":
        return any(x in src for x in ("uspto", "epo", "patent", "wipo")) or ident.startswith(("us", "ep", "wo"))
    if source_type == "literature":
        return any(
            x in src
            for x in ("literature", "arxiv", "semantic", "paper", "doi", "chemcrow-lit", "chemcrow_literature")
        ) or ident.startswith("doi:")
    if source_type == "internet":
        return any(x in src for x in ("web", "duck", "internet", "chemcrow-web", "chemcrow_web", "serp"))
    if source_type == "notebooklm":
        return "notebooklm" in src
    if source_type == "local":
        return src == "local" or "upload" in src or "ingest" in src
    return True


def _filter_evidence_by_types(evidence: list, source_types: list[str]) -> list:
    if not source_types:
        return evidence
    return [e for e in evidence if any(_evidence_matches_type(e, t) for t in source_types)]


def run_research(
    req: Requirement,
    pre_sources: list | None = None,
    source_types: list[str] | None = None,
    query: str = "",
) -> ResearchResult:
    """Run CRAG research graph; returns grounded evidence and recommended formulations."""
    from loguru import logger

    from .research_graph import graph_state_to_research_result, run_research_graph

    if source_types:
        logger.warning("run_research: source_types is deprecated; using ColBERT KB + federated fallback")

    q = query or req.headline()
    pre_index = list(pre_sources) if pre_sources else None
    # source_types no longer drives live retrieval, but an explicit filter still
    # constrains which pre-loaded sources are admitted (they surface verbatim).
    if pre_index and source_types:
        pre_index = _filter_evidence_by_types(pre_index, source_types) or None

    state = run_research_graph(
        topic=q,
        req=req,
        query=q,
        pre_index=pre_index,
        mode="recommend",
    )
    return graph_state_to_research_result(state, req)


def run_research_graph_stream(
    topic: str,
    req: Requirement | None = None,
    *,
    query: str = "",
    pre_index: list | None = None,
    event_cb=None,
):
    """SSE-friendly wrapper around CRAG graph."""
    from .research_graph import run_research_graph

    return run_research_graph(
        topic=topic,
        req=req,
        query=query or topic,
        pre_index=pre_index,
        progress_cb=event_cb,
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


def build_doe_factors(req: Requirement) -> list:
    """Collect DOE factors for a requirement (shared by workflow and baybe)."""
    from ..domain.project_spec import levers_to_doe_factors, normalize_requirement, resolve_levers
    from ..domain import knowledge

    req = normalize_requirement(req)
    base = req.active_formulation or knowledge.baseline_formulation(req)
    levers = resolve_levers(req, base if hasattr(base, "ingredients") else None)
    return levers_to_doe_factors(levers)


def build_doe(
    req: Requirement,
    design: str = "full_factorial",
    *,
    engine: str = "auto",
    n: int | None = None,
) -> DOEPlan:
    from ..services.engines.doe_registry import build_doe_plan

    factors = build_doe_factors(req)
    plan = build_doe_plan(factors, design=design, engine=engine, n=n)
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
    *,
    engine: str = "auto",
    campaign_state: str | None = None,
    existing_records: list | None = None,
    workbench_campaign_id: int | None = None,
) -> OptimizationResult:
    settings = get_settings()
    iterations = iterations or settings.optimize_iterations

    resolved = (engine or "auto").lower()
    if resolved in ("auto", "baybe"):
        from ..db.database import default_session_factory
        from ..services.engines.baybe_engine import BaybeCampaignEngine
        from ..services.engines.doe_registry import baybe_available

        if resolved == "baybe" or (resolved == "auto" and baybe_available()):
            try:
                baybe_eng = BaybeCampaignEngine()
                if baybe_eng.available():
                    from ..services.training import registry

                    records = existing_records if existing_records is not None else registry.records_for(req.domain)
                    with default_session_factory()() as db:
                        return baybe_eng.run_optimization(
                            req,
                            iterations=iterations,
                            campaign_state=campaign_state,
                            measurements=list(records),
                            progress_cb=progress_cb,
                            workbench_campaign_id=workbench_campaign_id,
                            db=db,
                        )
            except Exception:
                if resolved == "baybe":
                    raise

    from ..domain.project_spec import resolve_levers

    base = knowledge.baseline_formulation(req)
    levers = resolve_levers(req, req.active_formulation or base)
    factors = [
        Factor(name=l.name, low=l.low, high=l.high)
        for l in levers
    ]
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
