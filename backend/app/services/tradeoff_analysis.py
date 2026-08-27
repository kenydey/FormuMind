"""Pareto frontier and scenario picks for formulation candidates."""
from __future__ import annotations

import logging
import re
from typing import Iterable

from ..config import Settings, get_settings
from ..domain.schemas import Formulation, ObjectiveSpec, RecommendedFormula, Requirement
from ..domain.tradeoff_schemas import (
    ConfidenceLevel,
    FormulationCandidateView,
    GroundingSummary,
    ScenarioKind,
    ScenarioPick,
    TradeOffAnalysis,
    VerificationDoe,
)

logger = logging.getLogger(__name__)


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
        elif obj.direction == "match_target" and obj.target_value is not None:
            # Closer to target is better, so rank by negative distance — the
            # same "bigger is better" orientation minimize/maximize use below.
            y[:, j] = -np.abs(y[:, j] - obj.target_value)

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


def compute_pareto_ranks(
    values: list[list[float]],
    objectives: list[ObjectiveSpec],
) -> list[int | None]:
    """Non-dominated sorting: 0 = frontier, 1 = frontier once 0 is removed, …

    Peels successive frontiers off the remaining set. Needed both to report
    runner-up tiers to the user and as the selection primitive for
    evolutionary search, where ranking the *whole* population matters — a
    boolean frontier mask cannot order the individuals it excludes.

    Rows carrying NaN (a metric the predictor did not produce) never compare
    as dominated *or* dominating, so they occupy front 0 forever; the
    empty-front guard stops that from looping.
    """
    n = len(values)
    if n == 0:
        return []
    ranks: list[int | None] = [None] * n
    remaining = list(range(n))
    rank = 0
    while remaining:
        mask = compute_pareto_mask([values[i] for i in remaining], objectives)
        front = [i for i, keep in zip(remaining, mask) if keep]
        if not front:
            break
        for i in front:
            ranks[i] = rank
        front_set = set(front)
        remaining = [i for i in remaining if i not in front_set]
        rank += 1
    return ranks


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


def _verification_doe_for(
    form: Formulation,
    req,
    objectives: list[ObjectiveSpec],
    *,
    settings: Settings | None = None,
) -> VerificationDoe | None:
    """Build a minimal verification DOE anchored to one candidate.

    Reuses the mature ``build_doe`` (LHS, n=verification_doe_n) and annotates
    the plan notes with the reference candidate so the chemist knows what
    prediction this experiment validates. Returns None if disabled or on any
    failure (must never break the trade-off analysis).
    """
    settings = settings or get_settings()
    if not settings.verification_doe_enabled:
        return None
    try:
        from ..pipeline.workflow import build_doe

        plan = build_doe(req, "lhs", n=settings.verification_doe_n)
        plan.design = "verification"
        predicted = dict(form.predicted or {})
        targets = ", ".join(
            f"{o.metric}={predicted.get(o.metric)}" for o in objectives if o.metric in predicted
        )
        plan.notes = (
            f"验证 DOE — 参考基线候选「{form.name}」\n"
            f"目标确认：{targets or '（见候选预测）'}\n"
            f"建议：执行此 DOE 确认 {form.name} 的预测性能，达标后再放大批次。\n"
            + (plan.notes or "")
        )
        cid = ""
        # Reuse the same candidate id scheme as analyze_tradeoffs.
        return VerificationDoe(
            candidate_id=cid,
            candidate_name=form.name,
            note=f"验证候选「{form.name}」的预测性能（{targets or '见候选预测'}）",
            doe_plan=plan,
        )
    except Exception as exc:
        logger.debug("verification DOE skipped for %s: %s", form.name, exc)
        return None


def analyze_tradeoffs(
    forms: list[Formulation],
    objectives: list[ObjectiveSpec],
    rec_by_name: dict[str, RecommendedFormula] | None = None,
    *,
    scenario_kinds: Iterable[ScenarioKind] | None = None,
    req: "Requirement | None" = None,
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
        rec = rec_by_name.get(form.name)
        grounding = grounding_summary_from_rec(rec)
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

        cand_warnings = list(form.warnings or [])
        # 交叉校验：LLM 与 predictor 预测相差 >5× 时提示两套引擎不一致，
        # 避免 tradeoff 排序/场景结论建立在失真预测上而不自知。
        llm_pred = dict(rec.predicted or {}) if rec is not None else {}
        for metric in ("salt_spray_hours", "coating_weight_gsm", "film_weight_gsm"):
            pv = predicted.get(metric)
            lv = llm_pred.get(metric)
            if pv and lv and pv > 0 and lv > 0:
                ratio = lv / pv
                if ratio > 5.0 or ratio < 0.2:
                    cand_warnings.append(
                        f"{metric} 预测不一致：LLM {lv} vs predictor {pv}（{ratio:.1f}×，请人工核实）"
                    )

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
                warnings=cand_warnings,
            )
        )

    obj_for_pareto = objectives or []
    if obj_for_pareto:
        ranks = compute_pareto_ranks(values, obj_for_pareto)
    else:
        ranks = [0] * len(candidates)
    frontier_ids: list[str] = []
    for cand, rank in zip(candidates, ranks):
        # `pareto` and `pareto_frontier_ids` keep their old meaning (front 0
        # only); `pareto_rank` now also tiers the runners-up instead of being
        # None for everything that missed the frontier.
        cand.pareto = rank == 0
        cand.pareto_rank = rank
        if cand.pareto:
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

    # Third priority: minimal verification DOE per Pareto-front / scenario-pick
    # candidate, anchored to that candidate. Skip KG-incompatible candidates
    # (second-priority gate) — no point verifying a chemically-infeasible mix.
    verification_does: list[VerificationDoe] = []
    if req is not None and settings.verification_doe_enabled:
        picked_names: list[str] = []
        picked_names.extend(frontier_ids)  # frontier ids are candidate ids, map below
        scenario_names = [p.candidate_name for p in scenario_picks if p.candidate_name]
        # Map candidate id → form name; frontier_ids are candidate ids.
        id_to_form = {cand.id: cand.name for cand in candidates}
        verify_names = set(scenario_names)
        for cid in frontier_ids:
            nm = id_to_form.get(cid)
            if nm:
                verify_names.add(nm)
        seen: set[str] = set()
        for form in forms:
            if form.name not in verify_names or form.name in seen:
                continue
            seen.add(form.name)
            # Skip KG-incompatible (second-priority gate).
            _kc = getattr(form, "kg_compat", None)
            if _kc and not _kc.get("feasible", True):
                continue
            vdoe = _verification_doe_for(form, req, objectives, settings=settings)
            if vdoe is not None:
                # Backfill candidate id from the matched candidate.
                vdoe.candidate_id = next(
                    (c.id for c in candidates if c.name == form.name), vdoe.candidate_id
                )
                verification_does.append(vdoe)

    return TradeOffAnalysis(
        objectives=objectives,
        metric_columns=metric_columns,
        pareto_frontier_ids=frontier_ids,
        candidates=candidates,
        comparison_table=comparison_table,
        scenario_picks=scenario_picks,
        dominance_notes=notes,
        engine="predictor",
        verification_does=verification_does,
    )


def _best_performance_key(
    value: float | None, direction: str, target_value: float | None
) -> float:
    """Score a raw metric value so max() picks the objective's actual "best",
    matching the maximize/minimize/match_target orientation compute_pareto_mask
    uses (bigger transformed value = better)."""
    if value is None:
        return float("-inf")
    if direction == "minimize":
        return -value
    if direction == "match_target" and target_value is not None:
        return -abs(value - target_value)
    return value


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
        obj0 = objectives[0]
        metric = obj0.metric
        best = max(
            frontier,
            key=lambda c: _best_performance_key(
                c.predicted.get(metric), obj0.direction, obj0.target_value
            ),
        )
        val = best.predicted.get(metric)
        if obj0.direction == "minimize":
            desc = "预测最低"
        elif obj0.direction == "match_target" and obj0.target_value is not None:
            desc = f"预测最接近目标值 {obj0.target_value}"
        else:
            desc = "预测最高"
        picks.append(
            ScenarioPick(
                scenario="best_performance",
                candidate_id=best.id,
                candidate_name=best.name,
                rationale=f"Pareto 前沿；{metric} {desc}。",
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
