"""End-to-end orchestration: research -> recommend -> DOE -> simulate -> optimize.

This is the glue layer. It maps domain requirements onto the service adapters
and the DOE/optimizer engines, keeping a single source of truth for which
formulation levers are tuned per product family.
"""
from __future__ import annotations

import math
import threading
import uuid
from collections.abc import Callable

from ..config import get_settings
from ..domain import knowledge
from ..domain.chemistry import full_safety_check, validate_formulation
from ..domain.schemas import (
    DOEPlan,
    Formulation,
    ObjectiveSpec,
    OptimizationResult,
    ProductDomain,
    Requirement,
    ResearchResult,
)
from ..domain.research_query import build_research_query
from ..services import predictor
from ..services.optimizer import Factor, build_optimizer
from . import reconstruct

# Per-domain optimization objective metric to maximise.
OBJECTIVE: dict[ProductDomain, str] = {
    ProductDomain.anticorrosion_coating: "salt_spray_hours",
    ProductDomain.degreaser: "cleaning_efficiency",
    ProductDomain.surface_treatment: "salt_spray_hours",
    ProductDomain.autodeposition_coating: "salt_spray_hours",
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
    ProductDomain.autodeposition_coating: [
        ObjectiveSpec(metric="salt_spray_hours", weight=0.5, direction="maximize"),
        ObjectiveSpec(metric="cost_cny_per_kg", weight=0.25, direction="minimize"),
        ObjectiveSpec(metric="sustainability_idx", weight=0.25, direction="maximize"),
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
    *,
    chem_screen: bool = False,
    chem_screen_local: bool = False,
    objectives: list[ObjectiveSpec] | None = None,
    bounds: dict[str, tuple[float, float]] | None = None,
    enrich_network: bool = True,
) -> Formulation:
    from ..domain.project_spec import normalize_requirement, primary_objective

    req = normalize_requirement(req) if req else None
    # Enrich BEFORE predicting: the native chemistry gateway fills
    # cas/smiles/formula on ingredients, and predictor paths key off those
    # fields (SMILES-bearing ingredients take a structure-aware prediction
    # branch). Leaving enrich at the tail made form.score inconsistent with a
    # later re-score of the same returned object (multi_objective_score
    # re-predicts internally).
    from ..domain.formulation_gate import enrich_formulation

    form = enrich_formulation(form, network=enrich_network)
    form.predicted, form.predicted_std = predictor.predict_full(form, process, req=req)
    # Top-5‴ #2: optional soft-correct from workbench prediction_bias (default off).
    try:
        from ..services.prediction_bias_correct import soft_correct_predicted

        camp_id = getattr(req, "workbench_campaign_id", None) if req else None
        pid = getattr(req, "project_id", None) if req else None
        corrected, metrics = soft_correct_predicted(
            form.predicted,
            campaign_id=int(camp_id) if camp_id is not None else None,
            project_id=str(pid) if pid else None,
        )
        if metrics:
            form.predicted = corrected
            form.bias_corrected_metrics = list(metrics)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).debug("prediction bias soft-correct skipped: %s", exc)
    voc_limit = req.voc_limit_gpl if req else None
    # B-F1: 追加而非覆盖 —— form.warnings 此时已载有 grounding 阶段的
    # "已剔除低可信度成分"警告（formulation_gate.recommended_to_formulation 拷入），
    # 覆盖会让用户在卡片上永远看不到配方被动过删减。
    form.warnings = [*form.warnings, *validate_formulation(form, voc_limit_gpl=voc_limit)]
    voc_gpl = form.predicted.get("voc_gpl")
    form.warnings.extend(full_safety_check(form, voc_gpl=voc_gpl, voc_limit_gpl=voc_limit))
    if chem_screen:
        # Molecular patent / structure pre-screen (native molbloom/RDKit).
        # Only enabled on recommend paths — never inside optimization loops.
        from ..services import chemtools

        form.warnings.extend(chemtools.screen_formulation(form))
    if chem_screen_local:
        # P3: 零网络本地化学预筛（RDKit 价键 + molbloom patent）——
        # 优化循环安全版，每代数百次调用不触网。
        from ..services import chemtools

        form.warnings.extend(chemtools.screen_formulation_local(form))
    # `objectives` defaults to req.objectives for every existing caller. Callers
    # that already resolved a different objectives list to rank/select
    # candidates by (e.g. run_optimization falling back to default_objectives()
    # when req.objectives is empty) must pass it explicitly here too, or the
    # returned form.score silently reverts to a raw single-metric prediction —
    # a different number, on a different scale, than the one that actually
    # drove ranking/selection.
    if objectives is None:
        objectives = req.objectives if req else None
    if objectives:
        if predictor.score_is_raw(objectives):
            metric = objectives[0].metric
            form.score = float(form.predicted.get(metric, 0.0))
        else:
            # Same reasoning as objectives above: multi_objective_score
            # normalizes each metric against `bounds` before weighting, so
            # scoring with bounds seeded from this one formulation instead of
            # the range actually swept during search still leaves form.score
            # off the ranking score, just less drastically.
            if bounds is None:
                bounds = predictor.default_bounds(objectives, form)
            form.score = float(predictor.multi_objective_score(form, objectives, process, bounds))
    else:
        metric = primary_objective(req) if req else OBJECTIVE[form.domain]
        form.score = float(form.predicted.get(metric, 0.0))
    # A recipe whose weights do not add up to ~100 % (strict grounding drops unsupported
    # components instead of rescaling the rest) is not comparable with a complete one:
    # discount its score by the shared closure policy so it cannot out-rank a full recipe
    # on predicted properties alone. Complete recipes (|Σ−100| ≤ 0.5) are untouched.
    from ..domain.closure import discount_score

    form.score = discount_score(form.score, form.total_pct())
    if chem_screen:
        # KG soft ranking *after* score assignment so measured/INHIBITS factors
        # are not wiped by predicted / multi_objective assignment above.
        # Never runs inside optimization loops (chem_screen=False).
        from ..services.kg_recommend_score import kg_compat_adjust

        kg_compat_adjust(form, objectives=list(objectives) if objectives else None)
    # Batch C: always attach explain after score / optional KG adjust.
    try:
        from ..services.formulation_explain import attach_explain

        attach_explain(
            form,
            objectives=list(objectives) if objectives else None,
            requirement=req,
        )
    except Exception:
        pass
    # The same closure / safety message can arrive through more than one check.
    form.warnings = list(dict.fromkeys(form.warnings))
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
    if source_type == "surechembl":
        return "surechembl" in src
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

    q = build_research_query(query, req)
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
    result = graph_state_to_research_result(state, req)
    # The CRAG fallback runs a federated search whose sources come from
    # `federated_sources`, not from this argument, so patents and web hits could
    # arrive even when the caller asked for literature only. Whether that
    # happened depended on whether retrieval graded well enough to skip the
    # fallback — which in turn depended on which optional extras were installed,
    # so the same call returned different source types on different machines.
    # An explicit filter is a constraint on the answer, not just on the inputs.
    if source_types and result.evidence:
        filtered = _filter_evidence_by_types(result.evidence, source_types)
        if filtered:
            result.evidence = filtered
    return result


def _apply_levers(req: Requirement, values: dict[str, float | str]) -> Formulation:
    """Build a fresh formulation with lever ingredient percentages overridden."""
    return reconstruct.formulation_from_factors(req, values)


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
    seed: int | None = None,
    ccd_alpha: str | float | None = None,
) -> DOEPlan:
    from ..services import chemtools
    from ..services.engines.doe_registry import build_doe_plan

    factors = build_doe_factors(req)
    plan = build_doe_plan(
        factors, design=design, engine=engine, n=n, requirement=req, seed=seed, ccd_alpha=ccd_alpha
    )
    plan.plan_id = uuid.uuid4().hex
    plan.domain = req.domain
    review = chemtools.review_doe_factors(req, plan)
    if review:
        plan.notes = (plan.notes + "\n" if plan.notes else "") + "\n".join(review)
    # Persistent-KB parameter-space fusion: advisory literature envelopes for
    # factors documented in stored source guides (never mutates bounds).
    from ..services import kb_index

    kb_hints = kb_index.doe_parameter_hints([f.name for f in plan.factors])
    if kb_hints:
        plan.notes = (plan.notes + "\n" if plan.notes else "") + "\n".join(kb_hints)
    _cache_plan(plan)
    return plan


def _round_discrete_values(
    values: dict,
    discrete_levers: dict,
    base: "Formulation",
    process: dict,
) -> None:
    """U-3: fallback 优化器输出的离散因子最近邻取整（原地修改 values）。

    数值 levels 取最近值；字符串 levels 保持 baseline（不瞎猜）：
    先查 baseline 工艺字典 `process`，再查 baseline 配方同名成分，
    都没有才取 `levels[0]`。v9 从 run_optimization 内联抽出以便单测。
    """
    for _name, _lever in discrete_levers.items():
        if _name not in values:
            continue
        _v = values[_name]
        _num_levels = [lv for lv in _lever.levels if isinstance(lv, (int, float))]
        if _num_levels and isinstance(_v, (int, float)):
            values[_name] = min(_num_levels, key=lambda lv: abs(lv - _v))
        else:
            _base_val = process.get(_name)
            if _base_val is None:
                for _ing in getattr(base, "ingredients", []):
                    if _ing.name == _name:
                        _base_val = _ing.name
                        break
            values[_name] = _base_val if _base_val is not None else _lever.levels[0]


# Newest measured experiments shown to the fallback optimizer (it is O(n) per
# surrogate query, and old points matter less than recent ones).
_MAX_LAB_OBSERVATIONS = 200


def _lab_points(
    records: list | None,
    req: Requirement,
    factors: list[Factor],
    base: Formulation,
    levers: list,
    process: dict,
) -> list[tuple[list[float], dict[str, float]]]:
    """Measured lab experiments as ``(x, measured)`` in the optimizer's coordinates.

    ``x`` follows ``factors``: a lever the experiment varied takes its recorded
    value (clipped into range); one it did not record stays at the baseline
    recipe's value, which is what a DOE run that varies a subset of the levers
    actually did. Only ``source in REAL_SOURCES`` rows with measured values
    count, an experiment that recorded none of the optimizer's levers says
    nothing about this search space, and a project-scoped request ignores
    other projects'.
    """
    from ..domain.schemas import REAL_SOURCES

    baseline = reconstruct.baseline_lever_values(levers, base, process)
    pid = (req.project_id or "").strip()
    out: list[tuple[list[float], dict[str, float]]] = []
    for rec in records or []:
        try:
            # v13-3: workbench 真实测量也算 real。
            if getattr(rec, "source", "") not in REAL_SOURCES or not rec.measured:
                continue
            if rec.domain != req.domain:
                continue
            rec_pid = (getattr(rec, "project_id", "") or "").strip()
            if pid and rec_pid and rec_pid not in (pid, req.domain.value):
                continue
            x: list[float] = []
            covered = 0
            for f in factors:
                v = rec.factors.get(f.name)
                if v is None and f.name == "cure_temperature_c":
                    v = rec.cure_temperature_c
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                    x.append(f.clip(float(v)))
                    covered += 1
                else:
                    x.append(baseline[f.name])
            if covered == 0:
                continue
            measured = {
                m: float(val)
                for m, val in rec.measured.items()
                if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))
            }
            if measured:
                out.append((x, measured))
        except Exception:  # one malformed record must not sink the optimization
            continue
    return out[-_MAX_LAB_OBSERVATIONS:]


def run_optimization(
    req: Requirement,
    iterations: int | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    *,
    engine: str = "auto",
    campaign_state: str | None = None,
    existing_records: list | None = None,
    workbench_campaign_id: int | None = None,
    seed: int | None = None,
) -> OptimizationResult:
    settings = get_settings()
    iterations = iterations or settings.optimize_iterations

    resolved = (engine or "auto").lower()
    if resolved in ("auto", "baybe"):
        from ..services.engines.baybe_engine import BaybeCampaignEngine
        from ..services.engines.doe_registry import baybe_available

        if resolved == "baybe" or (resolved == "auto" and baybe_available()):
            try:
                baybe_eng = BaybeCampaignEngine()
                if baybe_eng.available():
                    from ..services.training import registry

                    records = existing_records if existing_records is not None else registry.records_for(req.domain)
                    return baybe_eng.run_optimization(
                        req,
                        iterations=iterations,
                        campaign_state=campaign_state,
                        measurements=list(records),
                        progress_cb=progress_cb,
                        workbench_campaign_id=workbench_campaign_id,
                        seed=seed,
                    )
            except Exception as exc:
                if resolved == "baybe":
                    raise
                # engine="auto": fall back to the numpy/optuna optimizer below,
                # but a real bug in the BayBE integration (bad measurement
                # shape, API mismatch, ...) must not vanish silently — without
                # this the caller only sees a quietly downgraded engine
                # ("numpy-ucb"/"optuna-tpe") with no trace BayBE was even tried.
                from loguru import logger

                logger.warning("BayBE optimization failed, falling back to numpy/optuna: {}", exc)

    from ..domain.project_spec import resolve_levers

    base = knowledge.baseline_formulation(req)
    levers = resolve_levers(req, req.active_formulation or base)
    # U-3: fallback 降级时离散因子不能当连续优化 —— 先记 warning，
    # 优化后对离散因子做最近邻取整。
    _discrete_levers = {l.name: l for l in levers if l.kind == "discrete" and l.levels}
    if _discrete_levers:
        from loguru import logger

        logger.warning(
            "Fallback optimizer degrading discrete levers to continuous: {}",
            sorted(_discrete_levers),
        )
    factors = [
        Factor(name=l.name, low=l.low, high=l.high)
        for l in levers
    ]
    opt = build_optimizer(factors=factors, seed=seed if seed is not None else 42)
    objective = OBJECTIVE[req.domain]
    objectives = req.objectives or default_objectives(req.domain)
    process = process_for(req)
    history: list[float] = []
    best_so_far = float("-inf")
    # Bounds seeded from objective ref ranges (or sensible defaults), not from a
    # single formulation's predicted values (which would collapse scores to ~0.5).
    bounds = predictor.default_bounds(objectives, base)

    def _score_candidate(x: list[float], measured: dict[str, float] | None = None) -> float:
        values = {f.name: v for f, v in zip(factors, x)}
        form = _apply_levers(req, values)
        process_it = dict(process)
        for k, v in values.items():
            if k in ("cure_temperature_c", "cure_time_min"):
                process_it[k] = v
        props = predictor.predict(form, process_it)
        if measured:
            # A measured value beats the model's guess; metrics the experiment did
            # not measure keep their prediction (a missing one would score as 0).
            props = {**props, **measured}
        # Expand running bounds.
        for metric, val in props.items():
            lo, hi = bounds.get(metric, (val, val))
            bounds[metric] = (min(lo, val), max(hi, val))
        # props is passed through: re-predicting the same formulation doubled the
        # (rdkit/thermo-heavy) predict cost of every iteration for the same result.
        return predictor.multi_objective_score(form, objectives, process_it, bounds, props=props)

    # Warm start: evaluate the incumbent (baseline) recipe first. Without it the
    # search only ever scores *suggested* points, so a short run — Optuna's TPE
    # draws its first ten trials at random — can return an "optimized" recipe
    # that is worse than the one the user already has. It is not one of the
    # ``iterations`` suggested experiments and adds no history entry, but it is
    # the starting best so the curve and the returned top are consistent.
    try:
        baseline_values = reconstruct.baseline_lever_values(levers, base, process)
        x0 = [baseline_values[f.name] for f in factors]
        score0 = _score_candidate(x0)
        opt.observe(x0, score0)
        best_so_far = score0
    except Exception as exc:  # warm start is an improvement, never a requirement
        from loguru import logger

        logger.warning("optimizer warm start with the baseline skipped: {}", exc)

    # Lab measurements: show the optimizer what was really measured. Before this
    # the numpy/optuna/BoTorch fallback ignored ``existing_records`` entirely (it
    # only learned through the retrained surrogate, which needs min_train_samples
    # per metric), while the BayBE path was seeded with them.
    lab_records = existing_records
    if lab_records is None:
        try:
            from ..services.training import registry

            lab_records = registry.records_for(req.domain, project_id=req.project_id or "")
        except Exception:  # registry trouble degrades to a virtual-only run
            lab_records = []
    lab_used = 0
    for x_lab, measured in _lab_points(lab_records, req, factors, base, levers, process):
        try:
            score_lab = _score_candidate(x_lab, measured)
            opt.observe(x_lab, score_lab)
            best_so_far = max(best_so_far, score_lab)
            lab_used += 1
        except Exception as exc:  # noqa: BLE001 — one bad point never aborts the run
            from loguru import logger

            logger.warning("lab observation skipped: {}", exc)

    for it in range(iterations):
        x = opt.suggest()
        score = _score_candidate(x)
        opt.observe(x, score)
        best_so_far = max(best_so_far, score)
        history.append(round(best_so_far, 3))
        if progress_cb:
            progress_cb((it + 1) / iterations, f"iter {it + 1}/{iterations}: best={best_so_far:.3f}")

    top: list[Formulation] = []
    for x, score in opt.ranked(settings.top_n_formulas):
        values = {f.name: v for f, v in zip(factors, x)}
        # U-3: 离散因子最近邻取整（抽成 helper 以便单测 —— v9）。
        _round_discrete_values(values, _discrete_levers, base, process)
        top_process = dict(process)
        for k, v in values.items():
            if k in ("cure_temperature_c", "cure_time_min"):
                top_process[k] = v
        # Offline / CI have no Redis or PubChem — network enrich of the top-N
        # ranked formulas is what left optimize tasks stuck at RUNNING after
        # progress hit 1.0. Local CAS/SMILES from the catalog still apply.
        form = _score_and_validate(
            _apply_levers(req, values),
            top_process,
            req,
            objectives=objectives,
            bounds=bounds,
            enrich_network=False,
        )
        # v10: 取整已改变配方 —— 展示用取整后的真实分数（form.score），
        # 而非取整前连续伪值的 score。
        form.name = f"Optimized {req.domain.value} (score {form.score:.3f})"
        top.append(form)
    # v10: 取整后按真实分数重排 —— opt.ranked() 的顺序是取整前伪值排的。
    top.sort(key=lambda f: f.score, reverse=True)
    # U-2: 血缘打通 —— 有真实 lab 测量时不再谎报 predictor_virtual。
    from ..services.doe_cycle_service import lab_measurement_source

    return OptimizationResult(
        iterations=iterations,
        objective=objective,
        objectives=objectives,
        history=history,
        top_formulations=top,
        engine=getattr(opt, "engine", "numpy-ucb"),
        # "lab" once measured data shaped this run (injected above) — also when the
        # caller passed none and the registry supplied it.
        measurement_source="lab" if lab_used else lab_measurement_source(lab_records or []),
        lab_points_used=lab_used,
    )
