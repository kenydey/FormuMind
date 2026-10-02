"""Shared post-processing for LLM formulation recommendations."""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict, deque
from typing import NamedTuple

from ..config import Settings, get_settings
from ..domain.schemas import Formulation, RecommendedFormula
from ..domain.tradeoff_schemas import TradeOffAnalysis
from . import chemtools
from .recommend_diversity import select_diverse_mmr
from .tradeoff_analysis import analyze_tradeoffs

logger = logging.getLogger(__name__)


class RecommendBundle(NamedTuple):
    """Result of :func:`run_recommend_orchestration`.

    ``warnings`` merges every stage's warnings (LLM synthesis + grounding +
    gates) so callers can no longer drop one on the floor.
    """

    aligned_formulas: list[RecommendedFormula]
    scored: list[Formulation]
    warnings: list[str]
    tradeoff: TradeOffAnalysis | None
    requested_n: int
    diversity_applied: bool
    engine: str
    # C-8: stable id for one recommendation round — returned to the client
    # in the API response and used to correlate adopt-outcome telemetry.
    recommend_id: str = ""


def run_recommend_orchestration(
    req,
    evidence: list,
    *,
    requested_n: int | None = None,
    objectives=None,
    include_tradeoff: bool = True,
    scenario_kinds=None,
    prefer_materials_catalog: bool = False,
    modify_prompt: str = "",
    base_formulas=None,
    synth_override=None,
    settings: Settings | None = None,
) -> RecommendBundle:
    """Single orchestration entry for formulation recommendation.

    Encapsulates the previously triplicated sequence shared by
    ``api/formulations.py::recommend_formulations``,
    ``research_graph.recommend_generate_node`` and
    ``research_graph.generate_node``:

    1. ``llm.recommend_formulations`` (LLM synthesis, offline fallback inside)
    2. ``ground_recommended_formulas`` (grounding against evidence)
    3. ``finalize_recommendation_bundle`` (score / dedupe / diversify / trade-off)

    Retrieval (Step 0) stays with the caller — the API path has its own
    hybrid/kb_only/llm_only mode handling and passes its synthesized response
    via ``synth_override`` (already carrying its mode-specific fallbacks).
    Response shaping stays with the caller — each entry keeps its own schema.
    """
    from ..services.grounded_recommend import ground_recommended_formulas

    settings = settings or get_settings()
    n = resolve_recommend_n(requested_n, settings=settings)
    # C-8: one id per recommendation round, shared by API response + telemetry.
    recommend_id = uuid.uuid4().hex

    if synth_override is not None:
        rec_resp = synth_override
    else:
        from ..services import llm

        llm_n = llm_candidate_count(n, settings=settings)
        rec_resp = llm.recommend_formulations(
            req,
            objectives,
            evidence,
            n=llm_n,
            modify_prompt=modify_prompt,
            base_formulas=base_formulas,
        )
    warnings: list[str] = list(rec_resp.warnings)

    grounded_formulas, ground_warnings = ground_recommended_formulas(
        rec_resp.formulas,
        evidence,
        prefer_materials_catalog=prefer_materials_catalog,
    )
    warnings.extend(ground_warnings)

    aligned, scored, gate_warnings, _, diversity_applied, tradeoff = (
        finalize_recommendation_bundle(
            grounded_formulas,
            req,
            evidence,
            requested_n=n,
            objectives=objectives,
            include_tradeoff=include_tradeoff,
            scenario_kinds=scenario_kinds,
            settings=settings,
        )
    )
    warnings.extend(gate_warnings)

    return RecommendBundle(
        aligned_formulas=aligned,
        scored=scored,
        warnings=warnings,
        tradeoff=tradeoff,
        requested_n=n,
        diversity_applied=diversity_applied,
        engine=rec_resp.engine,
        recommend_id=recommend_id,
    )


def resolve_recommend_n(requested: int | None, *, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    n = requested if requested is not None else settings.recommend_default_n
    return max(1, min(int(n), settings.recommend_max_n))


def llm_candidate_count(requested_n: int, *, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if settings.recommend_diversity_enabled and requested_n > 1:
        return min(requested_n * 2, settings.recommend_max_n)
    return requested_n


def finalize_scored_formulations(
    rec_formulas: list[RecommendedFormula],
    scored: list[Formulation],
    *,
    n: int,
    settings: Settings | None = None,
) -> tuple[list[Formulation], list[RecommendedFormula], list[str], bool]:
    settings = settings or get_settings()
    scored.sort(key=lambda f: (f.score or 0.0), reverse=True)
    scored, dedup_notes = chemtools.dedupe_similar_formulations(scored)

    diversity_applied = False
    if settings.recommend_diversity_enabled and len(scored) > n:
        scored, diversity_applied = select_diverse_mmr(
            scored,
            n,
            lambda_score=settings.recommend_diversity_lambda,
        )
    else:
        scored = scored[:n]

    name_to_recs: dict[str, deque] = defaultdict(deque)
    for r in rec_formulas:
        name_to_recs[r.name].append(r)
    formulas = []
    for f in scored:
        queue = name_to_recs.get(f.name)
        if queue:
            formulas.append(queue.popleft())

    return scored, formulas, dedup_notes, diversity_applied


def _rescore_with_shared_bounds(scored: list[Formulation], objectives, process) -> None:
    """Re-score a multi-objective batch against one shared normalisation range.

    ``_score_and_validate`` scores each candidate on its own, normalising every
    metric without a user-given range against ``(0, 2 × that candidate's own
    value)`` — so a weak and a strong candidate both land on exactly 0.5 and the
    ranking below (``finalize_scored_formulations`` sorts by ``score``) is just
    the LLM's output order. Candidates are only comparable on a common ruler,
    so recompute the aggregate with :func:`predictor.shared_bounds`.

    The KG compatibility adjustment was applied to ``score`` as a multiplier
    after the first scoring; it is carried over as ``score / first_base_score``
    rather than recomputed. Fail-open: on any error the per-candidate scores
    stay as they were.
    """
    import math

    if len(objectives or []) < 2 or not scored:
        return
    try:
        from . import predictor

        objectives = list(objectives)
        props = [predictor.predict(f, process) for f in scored]
        shared = predictor.shared_bounds(objectives, props)
        rescored: list[float] = []
        for form, p in zip(scored, props):
            old_base = predictor.multi_objective_score(
                form, objectives, process, predictor.default_bounds(objectives, form), props=p
            )
            new_base = predictor.multi_objective_score(
                form, objectives, process, shared, props=p
            )
            factor = 1.0
            if form.score is not None and old_base > 1e-9:
                factor = float(form.score) / old_base
            value = float(new_base) * factor
            if not math.isfinite(value):
                raise ValueError(f"non-finite rescored value for {form.name!r}")
            rescored.append(value)
        for form, value in zip(scored, rescored):
            form.score = value
    except Exception:
        logger.warning("shared-bounds rescoring failed; keeping per-candidate scores", exc_info=True)


def finalize_recommendation_bundle(
    rec_formulas: list[RecommendedFormula],
    req,
    evidence: list,
    *,
    requested_n: int | None = None,
    objectives=None,
    include_tradeoff: bool = True,
    scenario_kinds=None,
    settings: Settings | None = None,
) -> tuple[
    list[RecommendedFormula],
    list[Formulation],
    list[str],
    int,
    bool,
    TradeOffAnalysis | None,
]:
    """Score, dedupe, diversify, and optionally analyze trade-offs."""
    from ..domain.formulation_gate import recommended_to_formulation, validate_formulations
    from ..domain.objective_contract import normalize_objectives
    from ..pipeline.claim_checker import check_formulation_predictions
    from ..pipeline.workflow import _score_and_validate, process_for

    settings = settings or get_settings()
    n = resolve_recommend_n(requested_n, settings=settings)
    objectives = objectives or normalize_objectives(req)
    process = process_for(req)
    warnings: list[str] = []
    scored: list[Formulation] = []

    for rec in rec_formulas:
        try:
            form = recommended_to_formulation(rec)
            # ``objectives`` is the list trade-off analysis and the LLM prompt
            # use; scoring must rank by the same one (see _score_and_validate).
            scored.append(
                _score_and_validate(
                    form, process, req, chem_screen=True, objectives=objectives
                )
            )
        except ValueError as exc:
            warnings.append(str(exc))

    scored, gate_warnings = validate_formulations(scored, req=req)
    warnings.extend(gate_warnings)
    _rescore_with_shared_bounds(scored, objectives, process)
    for form in scored:
        warnings.extend(check_formulation_predictions(form, evidence))

    scored, aligned_formulas, dedup_notes, diversity_applied = finalize_scored_formulations(
        rec_formulas,
        scored,
        n=n,
        settings=settings,
    )
    warnings.extend(dedup_notes)

    tradeoff = None
    if include_tradeoff and settings.recommend_tradeoff_enabled:
        rec_map = {r.name: r for r in aligned_formulas}
        tradeoff = analyze_tradeoffs(
            scored,
            objectives,
            rec_map,
            scenario_kinds=scenario_kinds,
            req=req,
            settings=settings,
        )

    return aligned_formulas, scored, warnings, n, diversity_applied, tradeoff
