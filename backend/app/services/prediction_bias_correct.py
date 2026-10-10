"""Top-5‴ #2: soft-correct predicted metrics using workbench prediction_bias.

Corrected value = predicted − mean_error (where mean_error = predicted − measured
from training sync). Does **not** rewrite measured rows or Claims/DOE.
"""
from __future__ import annotations

import logging
from typing import Any

from ..config import get_settings

logger = logging.getLogger(__name__)

# P1-9: 各 metric 的物理下界（修正后钳制）。未列出的 metric 走名称启发式。
_PHYSICAL_LOWER_BOUNDS: dict[str, float] = {
    "salt_spray_h": 0.0,
    "cost_cny_per_kg": 0.0,
    "voc_gpl": 0.0,
}


def load_latest_bias_by_metric(
    *,
    campaign_id: int | None = None,
    project_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return ``by_metric`` from the newest ``prediction_bias`` loop_history entry."""
    camp = None
    try:
        from ..db.campaign_store import get_campaign_store

        store = get_campaign_store()
        if campaign_id is not None:
            camp = store.get_campaign_sync(int(campaign_id))
    except Exception as exc:
        logger.debug("bias load: campaign store unavailable: %s", exc)
        camp = None

    if camp is None and project_id:
        try:
            from sqlalchemy import select

            from ..db.database import default_session_factory
            from ..db.models import Campaign

            with default_session_factory()() as session:
                rows = (
                    session.execute(
                        select(Campaign)
                        .where(Campaign.project_id == str(project_id))
                        .order_by(Campaign.id.desc())
                        .limit(8)
                    )
                    .scalars()
                    .all()
                )
                for row in rows:
                    hist = list(row.loop_history or [])
                    for entry in reversed(hist):
                        if isinstance(entry, dict) and entry.get("type") == "prediction_bias":
                            by_metric = entry.get("by_metric") or {}
                            if isinstance(by_metric, dict) and by_metric:
                                return by_metric
        except Exception as exc:
            logger.debug("bias load: project campaign scan failed: %s", exc)
            return {}

    if camp is None:
        return {}

    history = list(getattr(camp, "loop_history", None) or [])
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "prediction_bias":
            continue
        by_metric = entry.get("by_metric") or {}
        if isinstance(by_metric, dict) and by_metric:
            return by_metric
    return {}


def soft_correct_predicted(
    predicted: dict[str, float],
    *,
    campaign_id: int | None = None,
    project_id: str | None = None,
    by_metric: dict[str, dict[str, Any]] | None = None,
    enabled: bool | None = None,
) -> tuple[dict[str, float], list[str]]:
    """Apply ``predicted − mean_error`` when flag on and n ≥ min_n.

    Returns ``(corrected_dict, list_of_corrected_metric_names)``.
    No-op when flag off or insufficient samples.

    ``enabled``: explicit override. When ``None``, fire if **global**
    ``prediction_bias_soft_correct`` **or** the project's workspace flag is
    true (post-A′ #4 — mirrors auto_loop / dossier auto_patch OR).
    """
    settings = get_settings()
    if enabled is None:
        enabled = bool(getattr(settings, "prediction_bias_soft_correct", False))
        if not enabled and project_id:
            try:
                from ..db.project_store import get_project_store

                detail = get_project_store().get(str(project_id).strip())
                ws = getattr(detail, "workspace", None) if detail is not None else None
                enabled = bool(getattr(ws, "prediction_bias_soft_correct", False))
            except Exception as exc:
                logger.debug("project soft_correct flag read failed: %s", exc)
                enabled = False
    if not enabled:
        return dict(predicted or {}), []
    min_n = int(getattr(settings, "prediction_bias_soft_correct_min_n", 3) or 3)
    bias = by_metric if by_metric is not None else load_latest_bias_by_metric(
        campaign_id=campaign_id, project_id=project_id
    )
    if not bias:
        return dict(predicted or {}), []

    out = dict(predicted or {})
    corrected: list[str] = []
    for metric, stats in bias.items():
        if not isinstance(stats, dict):
            continue
        if metric not in out:
            continue
        try:
            n = int(stats.get("n") or 0)
            mean_err = float(stats.get("mean_error"))
        except (TypeError, ValueError):
            continue
        if n < min_n:
            continue
        # P1-10: 异方差保护 —— rmse 远大于 |mean| 说明误差不是常数偏移，
        # 全局均值修正会恶化一侧，此时只告警不修正
        try:
            _rmse = float(stats.get("rmse") or 0)
            if _rmse > 2.0 * abs(mean_err) and _rmse > 0:
                logger.warning(
                    "bias correction skipped for %s: heteroscedastic "
                    "(rmse=%.3f >> |mean|=%.3f)",
                    metric, _rmse, abs(mean_err),
                )
                continue
        except (TypeError, ValueError):
            pass
        try:
            raw = float(out[metric])
        except (TypeError, ValueError):
            continue
        # P1-9: 物理下界钳制 —— 时长/浓度/百分比等 metric 修正后不能为负
        corrected_val = raw - mean_err
        _lower = _PHYSICAL_LOWER_BOUNDS.get(metric, None)
        if _lower is None:
            # 默认：已知非负物理量（时长、浓度、百分比、成本等）下界为 0
            _lower = 0.0 if any(
                k in metric for k in ("_h", "hour", "pct", "percent", "cost", "voc", "density", "viscosity", "concentration", "content")
            ) else None
        if _lower is not None and corrected_val < _lower:
            corrected_val = _lower
        out[metric] = round(corrected_val, 4)
        corrected.append(metric)
    return out, corrected
