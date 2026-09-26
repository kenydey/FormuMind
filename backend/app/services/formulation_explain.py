"""Build a structured ``Formulation.explain`` block for UI transparency (Batch C).

Does not change ranking — only attaches a display contract assembled from
already-computed fields (objectives, kg_compat, warnings, predicted_std,
bias_corrected_metrics, ingredient evidence_refs).
"""
from __future__ import annotations

from typing import Any, Iterable

from ..config import get_settings
from ..domain.schemas import Formulation, FormulationExplain, ObjectiveSpec, Requirement


def _objective_metrics(objectives: Iterable[ObjectiveSpec] | None) -> list[str]:
    out: list[str] = []
    for o in objectives or []:
        m = getattr(o, "metric", None) or getattr(o, "name", None)
        if m:
            out.append(str(m))
    return out


def _evidence_refs_from_ingredients(form: Formulation) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    refs: list[dict[str, str]] = []
    for ing in form.ingredients or []:
        for raw in getattr(ing, "evidence_refs", None) or []:
            s = str(raw or "").strip()
            if not s:
                continue
            if ":" in s:
                source_type, source_id = s.split(":", 1)
            else:
                source_type, source_id = "ref", s
            key = (source_type, source_id)
            if key in seen:
                continue
            seen.add(key)
            refs.append({"source_type": source_type, "source_id": source_id})
    return refs


def _kg_signals(form: Formulation) -> dict[str, Any]:
    kc = form.kg_compat or {}
    return {
        "feasible": bool(kc.get("feasible", True)) if kc else True,
        "status": str(kc.get("status") or ("unknown" if not kc else "ok")),
        "measured_materials": list(kc.get("measured_materials") or []),
        "measured_metric_hits": list(kc.get("measured_metric_hits") or []),
        "inhibits": [
            f"{p.get('a')}↔{p.get('b')}"
            for p in (kc.get("incompatible_pairs") or [])
            if isinstance(p, dict)
        ],
        "synergizes": [
            f"{p.get('a')}↔{p.get('b')}"
            for p in (kc.get("synergy_pairs") or [])
            if isinstance(p, dict)
        ],
    }


def _supply_flags(form: Formulation) -> list[str]:
    """Merge warning heuristics with A′ supplier commercial annotations."""
    try:
        from .supply_flags import collect_formulation_supply_flags

        names = [getattr(ing, "name", "") for ing in (form.ingredients or [])]
        return collect_formulation_supply_flags(
            names,
            existing_warnings=form.warnings or [],
        )
    except Exception:
        flags: list[str] = []
        for w in form.warnings or []:
            low = w.lower()
            if any(
                k in low
                for k in ("供应", "缺货", "交期", "discontinued", "restricted", "supply", "stale")
            ):
                flags.append(w)
        return flags[:8]


def _uncertainty(form: Formulation) -> list[str]:
    settings = get_settings()
    notes: list[str] = []
    if not getattr(settings, "recommend_uncertainty_flag", True):
        return notes
    stds = form.predicted_std or {}
    preds = form.predicted or {}
    for metric, std in stds.items():
        try:
            s = float(std)
            v = float(preds.get(metric) or 0.0)
        except (TypeError, ValueError):
            continue
        if s > 0 and (abs(v) < 1e-12 or s > abs(v) * 0.2):
            notes.append(f"{metric} 不确定性偏高 (±{s:g})")
    tiers = form.prediction_tiers or {}
    for metric, tier in tiers.items():
        t = str(tier or "").lower()
        if t in ("cold-start", "empirical", "surrogate", "rule"):
            notes.append(f"{metric} 来自 {tier}（非实测模型）")
    return notes[:12]


def _objectives_hit_miss(
    form: Formulation,
    objectives: list[ObjectiveSpec] | None,
) -> tuple[list[str], list[str]]:
    hits: list[str] = []
    misses: list[str] = []
    preds = form.predicted or {}
    for o in objectives or []:
        metric = str(getattr(o, "metric", None) or getattr(o, "name", None) or "")
        if not metric:
            continue
        if metric not in preds:
            misses.append(f"{metric}: 无预测值")
            continue
        try:
            val = float(preds[metric])
        except (TypeError, ValueError):
            misses.append(f"{metric}: 预测值无效")
            continue
        target = getattr(o, "target_value", None)
        if target is None:
            target = getattr(o, "target", None)
        direction = str(getattr(o, "direction", None) or "maximize").lower()
        lower = getattr(o, "ref_min", None)
        if lower is None:
            lower = getattr(o, "lower", None)
        upper = getattr(o, "ref_max", None)
        if upper is None:
            upper = getattr(o, "upper", None)
        ok = True
        detail = f"{metric}={val:g}"
        if lower is not None and val < float(lower):
            ok = False
            detail += f" < ref_min {lower}"
        if upper is not None and val > float(upper):
            ok = False
            detail += f" > ref_max {upper}"
        if target is not None and lower is None and upper is None:
            t = float(target)
            if t != 0 and abs(val - t) / abs(t) > 0.35:
                ok = False
                detail += f" 偏离目标 {t:g}"
            elif direction == "minimize" and val > t * 1.25:
                ok = False
                detail += f" 高于目标 {t:g}"
            elif direction == "maximize" and val < t * 0.75:
                ok = False
                detail += f" 低于目标 {t:g}"
        (hits if ok else misses).append(detail)
    # Metric hits from KG measured edges count as soft objective support
    kc = form.kg_compat or {}
    for h in kc.get("measured_metric_hits") or []:
        if not isinstance(h, dict):
            continue
        if h.get("quality") == "good":
            label = f"KG实测 {h.get('material')}→{h.get('metric')}"
            if label not in hits:
                hits.append(label)
    return hits, misses


def build_formulation_explain(
    form: Formulation,
    *,
    objectives: list[ObjectiveSpec] | None = None,
    requirement: Requirement | None = None,
) -> FormulationExplain:
    """Assemble explain block and attach to ``form.explain``."""
    objs = list(objectives) if objectives is not None else None
    if objs is None and requirement is not None:
        objs = list(getattr(requirement, "objectives", None) or [])

    hits, misses = _objectives_hit_miss(form, objs)
    bias = list(getattr(form, "bias_corrected_metrics", None) or [])
    notes: list[str] = []
    if form.rationale:
        notes.append(form.rationale[:240])
    for w in (form.warnings or [])[:6]:
        if w not in notes:
            notes.append(w)

    effect_trace: list[dict[str, Any]] = []
    if requirement is not None:
        try:
            from .requirement_effect_trace import build_requirement_effect_trace

            effect_trace = build_requirement_effect_trace(requirement)
        except Exception:
            effect_trace = []

    explain = FormulationExplain(
        objectives_hit=hits,
        constraints_miss=misses,
        evidence_refs=_evidence_refs_from_ingredients(form),
        kg_signals=_kg_signals(form),
        supply_flags=_supply_flags(form),
        uncertainty=_uncertainty(form),
        bias_corrected=bool(bias),
        bias_corrected_metrics=bias,
        notes=notes,
        effect_trace=effect_trace,
    )
    form.explain = explain
    return explain


def attach_explain(
    form: Formulation,
    *,
    objectives: list[ObjectiveSpec] | None = None,
    requirement: Requirement | None = None,
) -> Formulation:
    build_formulation_explain(form, objectives=objectives, requirement=requirement)
    return form
