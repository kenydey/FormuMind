"""Shared post-processing for LLM formulation recommendations."""
from __future__ import annotations

import logging

from ..config import Settings, get_settings
from ..domain.schemas import Formulation, RecommendedFormula
from ..domain.tradeoff_schemas import TradeOffAnalysis
from . import chemtools
from .recommend_diversity import select_diverse_mmr
from .tradeoff_analysis import analyze_tradeoffs

logger = logging.getLogger(__name__)


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

    selected_names = {f.name for f in scored}
    formulas = [r for r in rec_formulas if r.name in selected_names]
    name_order = {f.name: i for i, f in enumerate(scored)}
    formulas.sort(key=lambda r: name_order.get(r.name, 999))

    return scored, formulas, dedup_notes, diversity_applied


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
            scored.append(_score_and_validate(form, process, req, chem_screen=True))
        except ValueError as exc:
            warnings.append(str(exc))

    scored, gate_warnings = validate_formulations(scored, req=req)
    warnings.extend(gate_warnings)
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
            settings=settings,
        )

    return aligned_formulas, scored, warnings, n, diversity_applied, tradeoff
