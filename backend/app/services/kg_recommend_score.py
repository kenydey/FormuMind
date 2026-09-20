"""KG → recommendation ranking adapter (second priority).

Turns KG material-compatibility signals into *soft* score adjustments on a
candidate ``Formulation``:

* An ``INHIBITS`` relation between two materials in the formulation skeleton
  multiplies ``form.score`` by ``settings.kg_inhibits_penalty`` (default 0.5)
  and appends a human-readable warning. The candidate sinks in the ranking but
  is never deleted — transparency over hard blocking.
* A ``SYNERGIZES`` relation (only when ``kg_synergizes_bonus > 1.0``) gives a
  mild multiplicative bonus. Disabled by default.
* **Metric-aware measured edges** (2026-09-16): when ``objectives`` are passed,
  ``measured_performance`` links ``mat:* → prop:{metric}`` for those target
  metrics drive good/presence/poor soft factors. Unrelated measured edges no
  longer earn the full blanket bonus. Callers must apply this *after*
  ``form.score`` is assigned from predicted / multi-objective scoring.

This complements the first-priority hard ``infeasible`` gate used inside the
DOE generation loop: the recommend path is soft (ranking), the loop path is
hard (candidate marking). Both consume the same deterministic KG source.

KG disabled (``kg_enabled is False``) → no-op, score untouched.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from ..config import get_settings
from ..domain.schemas import Formulation, ObjectiveSpec
from .kg_chemical_check import ChemicalCheckResult, check_formulation_chemistry, _resolve_entity_id

logger = logging.getLogger(__name__)

_VALUE_IN_SENTENCE = re.compile(r"=\s*([-+]?\d+(?:\.\d+)?)")


def _resolve_prop_id(metric: str) -> str | None:
    """Best-effort resolve objective metric → property entity id."""
    if not metric:
        return None
    from ..db.entity_store import get_entity_store

    store = get_entity_store()
    direct = f"prop:{metric}"
    row = store.get_entity(direct)
    if row is not None:
        return row.id
    hits = store.search_entities(metric, limit=5)
    for h in hits:
        kind = (getattr(h, "kind", None) or "").lower()
        hid = getattr(h, "id", "") or ""
        if kind == "property" or hid.startswith("prop:"):
            return hid
    return hits[0].id if hits else None


def _raw_measured_value(link) -> tuple[float | None, float]:
    """Return ``(measured_value or None, confidence)`` from a measured link."""
    conf = float(getattr(link, "confidence", None) or 0.5)
    refs = getattr(link, "evidence_refs", None) or []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        method = (ref.get("extraction_method") or "").lower()
        if method and method != "measured":
            continue
        if "measured_value" in ref:
            try:
                return float(ref["measured_value"]), conf
            except (TypeError, ValueError):
                pass
        sent = ref.get("sentence") or ""
        m = _VALUE_IN_SENTENCE.search(sent)
        if m:
            try:
                return float(m.group(1)), conf
            except ValueError:
                pass
    return None, conf


def _classify_quality(
    *,
    direction: str,
    value: float | None,
    confidence: float,
    predicted: float | None,
) -> str:
    """Return ``good`` | ``poor`` | ``presence`` for a single measured hit."""
    direction = (direction or "maximize").lower()
    if value is not None and predicted is not None and abs(float(predicted)) > 1e-12:
        pred = float(predicted)
        ratio = float(value) / pred
        if direction == "minimize":
            if ratio <= 1.1:
                return "good"
            if ratio >= 2.0:
                return "poor"
        else:
            if ratio >= 0.9:
                return "good"
            if ratio <= 0.5:
                return "poor"
        return "presence"
    # No predicted baseline — use stored confidence band.
    if confidence >= 0.7:
        return "good"
    if confidence < 0.55:
        return "poor"
    return "presence"


def collect_measured_metric_hits(
    form: Formulation,
    objectives: Iterable[ObjectiveSpec] | None,
) -> list[dict[str, Any]]:
    """Collect mat→prop measured hits for the given objective metrics."""
    objs = [o for o in (objectives or []) if getattr(o, "metric", None)]
    if not objs:
        return []

    materials = [i.name for i in form.ingredients if i.name]
    if not materials:
        return []

    from ..db.entity_store import get_entity_store

    store = get_entity_store()
    prop_by_metric: dict[str, str] = {}
    for obj in objs:
        pid = _resolve_prop_id(obj.metric)
        if pid:
            prop_by_metric[obj.metric] = pid
    if not prop_by_metric:
        return []

    direction_by_metric = {o.metric: (o.direction or "maximize") for o in objs}
    predicted = getattr(form, "predicted", None) or {}
    hits: list[dict[str, Any]] = []

    for name in materials:
        eid = _resolve_entity_id(name)
        if not eid:
            continue
        try:
            links = store.get_links_for_entity(
                eid,
                direction="out",
                link_types=["measured_performance"],
                limit=100,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("measured metric lookup failed for %s: %s", name, exc)
            continue
        for link in links:
            if getattr(link, "extraction_method", None) != "measured":
                continue
            dst = getattr(link, "dst_entity_id", None)
            metric = next((m for m, pid in prop_by_metric.items() if pid == dst), None)
            if metric is None:
                # evidence may carry metric even if prop id differs
                refs = getattr(link, "evidence_refs", None) or []
                for ref in refs:
                    if isinstance(ref, dict) and ref.get("metric") in prop_by_metric:
                        metric = ref["metric"]
                        break
            if metric is None:
                continue
            value, conf = _raw_measured_value(link)
            pred = predicted.get(metric)
            if isinstance(pred, (int, float)):
                pred_f: float | None = float(pred)
            else:
                pred_f = None
            quality = _classify_quality(
                direction=direction_by_metric.get(metric, "maximize"),
                value=value,
                confidence=conf,
                predicted=pred_f,
            )
            hits.append(
                {
                    "material": name,
                    "metric": metric,
                    "prop_id": dst,
                    "value": value,
                    "confidence": conf,
                    "quality": quality,
                }
            )
    return hits


def _best_quality(hits: list[dict[str, Any]]) -> str | None:
    if not hits:
        return None
    ranks = {"good": 3, "presence": 2, "poor": 1}
    return max(hits, key=lambda h: ranks.get(h.get("quality") or "", 0)).get("quality")


def kg_compat_adjust(
    form: Formulation,
    *,
    objectives: list[ObjectiveSpec] | None = None,
) -> ChemicalCheckResult:
    """Apply KG compatibility adjustments to ``form.score`` in place.

    Prefer calling **after** ``form.score`` has been assigned from predicted /
    multi-objective scoring, and pass ``objectives`` so measured edges for the
    target metrics can drive ranking. Returns the underlying
    ``ChemicalCheckResult`` (plus metric hits recorded on ``form.kg_compat``).
    """
    settings = get_settings()
    if not settings.kg_enabled:
        return ChemicalCheckResult(feasible=True, status="pass")

    chk = check_formulation_chemistry(form, include_synergies=True)

    penalty = float(settings.kg_inhibits_penalty)
    bonus = float(settings.kg_synergizes_bonus)
    measured_bonus = float(getattr(settings, "kg_measured_bonus", 1.15))
    metric_bonus = float(getattr(settings, "kg_measured_metric_bonus", 1.12))
    metric_penalty = float(getattr(settings, "kg_measured_metric_penalty", 0.92))
    metric_presence = float(getattr(settings, "kg_measured_metric_presence", 1.05))

    metric_hits = collect_measured_metric_hits(form, objectives)
    best_q = _best_quality(metric_hits)

    if not chk.feasible:
        # INHIBITS hit → sink the candidate; never award measured bonuses.
        if form.score is not None and penalty < 1.0:
            form.score = float(form.score) * penalty
        form.warnings.append(
            "知识图谱化学相容性告警：" + "；".join(chk.reasons)
        )
    elif bonus > 1.0 and chk.synergy_pairs:
        if form.score is not None:
            form.score = float(form.score) * bonus

    if chk.feasible and form.score is not None:
        if best_q == "good" and metric_bonus > 1.0:
            form.score = float(form.score) * metric_bonus
            mats = sorted({h["material"] for h in metric_hits if h.get("quality") == "good"})
            metrics = sorted({h["metric"] for h in metric_hits if h.get("quality") == "good"})
            form.warnings.append(
                "目标指标实测加成："
                + "、".join(mats)
                + f"（指标 {'/'.join(metrics)}）"
            )
        elif best_q == "poor" and metric_penalty < 1.0:
            form.score = float(form.score) * metric_penalty
            mats = sorted({h["material"] for h in metric_hits if h.get("quality") == "poor"})
            metrics = sorted({h["metric"] for h in metric_hits if h.get("quality") == "poor"})
            form.warnings.append(
                "目标指标实测偏弱降权："
                + "、".join(mats)
                + f"（指标 {'/'.join(metrics)}）"
            )
        elif best_q == "presence" and metric_presence > 1.0:
            form.score = float(form.score) * metric_presence
            mats = sorted({h["material"] for h in metric_hits})
            form.warnings.append(
                "目标指标实测存在加成：" + "、".join(mats)
            )
        elif not metric_hits and chk.measured_materials and measured_bonus > 1.0:
            # Legacy blanket only when the caller did not ask for target metrics.
            # With objectives, unrelated measured edges must not outrank a
            # metric-aware miss (or a weaker target hit).
            if not objectives:
                form.score = float(form.score) * measured_bonus
                form.warnings.append(
                    "实测验证加成：配方含实测验证材料 " + "、".join(chk.measured_materials)
                )

    record_kg_compat(form, chk, measured_metric_hits=metric_hits)
    return chk


def record_kg_compat(
    form: Formulation,
    chk: ChemicalCheckResult,
    *,
    measured_metric_hits: list[dict[str, Any]] | None = None,
) -> None:
    """Stash KG adjustment detail on the formulation for UI transparency."""
    form.kg_compat = {
        "feasible": chk.feasible,
        "status": chk.status,
        "incompatible_pairs": [
            {"a": a, "b": b, "relation": rel} for a, b, rel in chk.incompatible_pairs
        ],
        "synergy_pairs": [
            {"a": a, "b": b, "relation": rel} for a, b, rel in chk.synergy_pairs
        ],
        "measured_materials": list(chk.measured_materials),
        "measured_metric_hits": list(measured_metric_hits or []),
        "reasons": chk.reasons,
    }
