"""Pareto frontier and scenario picks for formulation candidates."""
from __future__ import annotations

import re
from typing import Iterable

from ..config import Settings, get_settings
from ..domain.schemas import Formulation, ObjectiveSpec, ProductDomain, RecommendedFormula
from ..domain.tradeoff_schemas import (
    ConfidenceLevel,
    FormulationCandidateView,
    GroundingSummary,
    ScenarioKind,
    ScenarioPick,
    TradeOffAnalysis,
)


def candidate_id(name: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "candidate").lower()).strip("-")[:48]
    return slug or f"f-{index:03d}"


def grounding_summary_from_rec(rec: RecommendedFormula | None) -> GroundingSummary:
    if rec is None:
        return GroundingSummary()
    high = low = 0
    low_names: list[str] = []
    refs: list[str] = []
    for comp in rec.components:
        if comp.grounding_confidence == "low":
            low += 1
            low_names.append(comp.name)
        else:
            high += 1
        refs.extend(comp.evidence_refs or [])
    return GroundingSummary(
        high_count=high,
        low_count=low,
        low_component_names=low_names[:8],
        evidence_refs=list(dict.fromkeys(refs))[:12],
    )


def compute_pareto_mask(
    values: list[list[float]],
    objectives: list[ObjectiveSpec],
) -> list[bool]:
    n = len(values)
    if n == 0:
        return []
    if not objectives:
        return [True] * n

    import numpy as np

    y = np.array(values, dtype=float)
    for j, obj in enumerate(objectives):
        if j >= y.shape[1]:
            break
        if obj.direction == "minimize":
            y[:, j] = -y[:, j]

    is_pareto = np.ones(n, dtype=bool)
    for i in range(n):
        if not is_pareto[i]:
            continue
        for k in range(n):
            if i == k or not is_pareto[k]:
                continue
            if np.all(y[i] >= y[k]) and np.any(y[i] > y[k]):
                is_pareto[k] = False
            elif np.all(y[k] >= y[i]) and np.any(y[k] > y[i]):
                is_pareto[i] = False
                break
    return is_pareto.tolist()


def _confidence(form: Formulation, grounding: GroundingSummary) -> ConfidenceLevel:
    settings = get_settings()
    if not settings.recommend_uncertainty_flag:
        return "medium"
    primary = next(iter(form.predicted.values()), None)
    primary_key = next(iter(form.predicted.keys()), "")
    std = form.predicted_std.get(primary_key)
    if primary and std and primary > 0 and std / primary > 0.25:
        return "low"
    if grounding.low_count > 0:
        return "medium"
    return "high"


def analyze_tradeoffs(
    forms: list[Formulation],
    objectives: list[ObjectiveSpec],
    rec_by_name: dict[str, RecommendedFormula] | None = None,
    *,
    scenario_kinds: Iterable[ScenarioKind] | None = None,
    settings: Settings | None = None,
) -> TradeOffAnalysis | None:
    settings = settings or get_settings()
    if not settings.recommend_tradeoff_enabled or not forms:
        return None

    rec_by_name = rec_by_name or {}
    metrics = [o.metric for o in objectives if o.metric]
    if "cost_cny_per_kg" not in metrics:
        metrics.append("cost_cny_per_kg")
    metric_columns = list(dict.fromkeys(metrics + ["score"]))

    candidates: list[FormulationCandidateView] = []
    values: list[list[float]] = []

    for idx, form in enumerate(forms):
        cid = candidate_id(form.name, idx)
        grounding = grounding_summary_from_rec(rec_by_name.get(form.name))
        predicted = dict(form.predicted or {})
        cost = predicted.get("cost_cny_per_kg")
        if cost is None and form.ingredients:
            try:
                from ..services import predictor

                props = predictor.predict(form, None)
                predicted.update(props)
                cost = props.get("cost_cny_per_kg")
            except Exception:
                pass

        row_vals: list[float] = []
        for m in objectives:
            val = predicted.get(m.metric)
            row_vals.append(float(val) if val is not None else float("nan"))
        values.append(row_vals)

        candidates.append(
            FormulationCandidateView(
                id=cid,
                name=form.name,
                predicted=predicted,
                predicted_std=dict(form.predicted_std or {}),
                cost_cny_per_kg=float(cost) if cost is not None else None,
                score=form.score,
                confidence=_confidence(form, grounding),
                grounding=grounding,
                warnings=list(form.warnings or []),
            )
        )

    obj_for_pareto = objectives or []
    pareto_mask = compute_pareto_mask(values, obj_for_pareto) if obj_for_pareto else [True] * len(candidates)
    frontier_ids: list[str] = []
    for cand, is_pf in zip(candidates, pareto_mask):
        cand.pareto = bool(is_pf)
        cand.pareto_rank = 0 if is_pf else None
        if is_pf:
            frontier_ids.append(cand.id)

    comparison_table: list[dict[str, object]] = []
    for cand in candidates:
        row: dict[str, object] = {
            "id": cand.id,
            "name": cand.name,
            "score": cand.score,
            "pareto": cand.pareto,
            "confidence": cand.confidence,
            "grounding_low_count": cand.grounding.low_count,
        }
        for col in metric_columns:
            if col == "score":
                continue
            row[col] = cand.predicted.get(col)
        comparison_table.append(row)

    kinds = list(scenario_kinds or ["best_performance", "lowest_cost", "balanced"])
    scenario_picks = _build_scenario_picks(candidates, objectives, kinds)

    notes: list[str] = []
    if frontier_ids:
        notes.append(
            f"{len(frontier_ids)}/{len(candidates)} 候选位于 Pareto 前沿"
            + (f"（{' × '.join(o.metric for o in objectives)}）。" if objectives else "。")
        )

    return TradeOffAnalysis(
        objectives=objectives,
        metric_columns=metric_columns,
        pareto_frontier_ids=frontier_ids,
        candidates=candidates,
        comparison_table=comparison_table,
        scenario_picks=scenario_picks,
        dominance_notes=notes,
        engine="predictor",
    )


def _build_scenario_picks(
    candidates: list[FormulationCandidateView],
    objectives: list[ObjectiveSpec],
    kinds: list[ScenarioKind],
) -> list[ScenarioPick]:
    if not candidates:
        return []

    picks: list[ScenarioPick] = []
    frontier = [c for c in candidates if c.pareto] or list(candidates)

    if "best_performance" in kinds and objectives:
        metric = objectives[0].metric
        best = max(frontier, key=lambda c: c.predicted.get(metric, float("-inf")))
        val = best.predicted.get(metric)
        picks.append(
            ScenarioPick(
                scenario="best_performance",
                candidate_id=best.id,
                candidate_name=best.name,
                rationale=f"Pareto 前沿；{metric} 预测最高。",
                primary_metric=metric,
                primary_value=float(val) if val is not None else None,
            )
        )

    if "lowest_cost" in kinds:
        cheapest = min(
            candidates,
            key=lambda c: c.cost_cny_per_kg if c.cost_cny_per_kg is not None else float("inf"),
        )
        picks.append(
            ScenarioPick(
                scenario="lowest_cost",
                candidate_id=cheapest.id,
                candidate_name=cheapest.name,
                rationale="单位成本最低。",
                primary_metric="cost_cny_per_kg",
                primary_value=cheapest.cost_cny_per_kg,
            )
        )

    if "balanced" in kinds:
        balanced = max(frontier, key=lambda c: c.score or float("-inf"))
        picks.append(
            ScenarioPick(
                scenario="balanced",
                candidate_id=balanced.id,
                candidate_name=balanced.name,
                rationale="Pareto 前沿内加权 score 最高。",
                primary_metric="score",
                primary_value=balanced.score,
            )
        )

    if "low_voc" in kinds:
        voc_candidates = [c for c in candidates if c.predicted.get("voc_gpl") is not None]
        if voc_candidates:
            low_voc = min(voc_candidates, key=lambda c: c.predicted.get("voc_gpl", float("inf")))
            picks.append(
                ScenarioPick(
                    scenario="low_voc",
                    candidate_id=low_voc.id,
                    candidate_name=low_voc.name,
                    rationale="VOC 预测最低。",
                    primary_metric="voc_gpl",
                    primary_value=float(low_voc.predicted.get("voc_gpl", 0)),
                )
            )

    return picks
