"""Workbench completed rows → ExperimentRecord training pipeline (Sprint 1)."""
from __future__ import annotations

import logging

from ..config import get_settings
from ..db.campaign_types import WorkbenchRow
from ..db.models import Campaign
from ..domain.schemas import ExperimentRecord, ProductDomain

logger = logging.getLogger(__name__)


def workbench_record_label(campaign_id: int, item_id: str) -> str:
    return f"wb:{campaign_id}:{item_id}"


def _numeric_measured(measurements: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, val in (measurements or {}).items():
        if val is None or val == "":
            continue
        try:
            out[str(key)] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def row_to_experiment_record(
    row: WorkbenchRow,
    *,
    campaign_id: int,
    domain: ProductDomain,
    project_id: str = "",
) -> ExperimentRecord | None:
    if row.status != "Completed":
        return None
    measured = _numeric_measured(row.measurements)
    if not measured:
        return None

    merged = {**(row.planned_params or {}), **(row.actual_params or {})}
    factors: dict[str, float] = {}
    cure_temp: float | None = None
    for key, val in merged.items():
        if key == "cure_temperature_c":
            try:
                cure_temp = float(val)
            except (TypeError, ValueError):
                pass
            continue
        try:
            factors[str(key)] = float(val)
        except (TypeError, ValueError):
            continue

    return ExperimentRecord(
        domain=domain,
        project_id=project_id or "",
        factors=factors,
        cure_temperature_c=cure_temp,
        measured=measured,
        source="workbench",
        label=workbench_record_label(campaign_id, row.item_id),
    )


def _campaign_domain(campaign: Campaign) -> ProductDomain:
    # Campaign metadata does not store domain; anticorrosion is the primary use case.
    return ProductDomain.anticorrosion_coating


def _compute_prediction_bias(
    to_add: list[ExperimentRecord],
    domain: ProductDomain,
    project_id: str,
) -> dict:
    """Compute predicted vs measured bias using the *current* registry (before retrain).

    Returns ``{n_rows, by_metric: {metric: {n, mean_error, rmse, mae, max_abs}}}``
    or ``{}`` when no metric has a trained model yet.
    """
    if not to_add:
        return {}
    from ..domain import features
    from ..domain.schemas import Requirement, Substrate
    from ..pipeline import reconstruct
    from .training import registry

    per_metric_errors: dict[str, list[float]] = {}
    for rec in to_add:
        # Build feature vector exactly as training does
        req = Requirement(domain=rec.domain)
        sub_raw = rec.factors.get("substrate")
        if sub_raw is not None:
            try:
                req.substrate = Substrate(str(sub_raw))
            except Exception:
                pass
        try:
            form = reconstruct.formulation_from_factors(req, rec.factors)
            vec = features.vector(form, {"cure_temperature_c": rec.cure_temperature_c or 0.0})
        except Exception:
            continue
        for metric, measured in rec.measured.items():
            try:
                res = registry.predict(domain, metric, vec, project_id=project_id)
            except Exception:
                continue
            if res is None:
                continue
            pred, _n = res
            err = float(pred) - float(measured)  # predicted - measured
            per_metric_errors.setdefault(metric, []).append(err)

    if not per_metric_errors:
        return {}

    import math as _m

    by_metric: dict[str, dict] = {}
    for metric, errs in per_metric_errors.items():
        n = len(errs)
        mean_err = sum(errs) / n
        mse = sum(e * e for e in errs) / n
        rmse = _m.sqrt(mse)
        mae = sum(abs(e) for e in errs) / n
        max_abs = max(abs(e) for e in errs)
        by_metric[metric] = {
            "n": n,
            "mean_error": round(mean_err, 4),
            "rmse": round(rmse, 4),
            "mae": round(mae, 4),
            "max_abs": round(max_abs, 4),
        }

    return {"n_rows": len(to_add), "by_metric": by_metric}


def ingest_workbench_rows(
    campaign_id: int,
    rows: list[WorkbenchRow],
    *,
    retrain: bool = True,
) -> dict:
    """Idempotently push Completed workbench rows into ModelRegistry."""
    settings = get_settings()
    if not settings.workbench_auto_train:
        return {"ingested": 0, "skipped": 0, "message": "workbench_auto_train disabled"}

    from ..db.campaign_store import get_campaign_store
    from .training import registry

    store = get_campaign_store()
    campaign = store.get_campaign_sync(campaign_id)
    if campaign is None:
        return {"ingested": 0, "skipped": 0, "message": "campaign not found"}

    domain = _campaign_domain(campaign)
    project_id = (campaign.project_id or "").strip()
    known = registry.known_labels()

    to_add: list[ExperimentRecord] = []
    skipped = 0
    for row in rows:
        rec = row_to_experiment_record(
            row, campaign_id=campaign_id, domain=domain, project_id=project_id
        )
        if rec is None:
            continue
        if rec.label in known:
            skipped += 1
            continue
        to_add.append(rec)
        known.add(rec.label)

    if to_add:
        # P2: compute bias BEFORE retrain (use current model as baseline)
        bias = _compute_prediction_bias(to_add, domain, project_id)
        registry.add(to_add, retrain=retrain)
        logger.info(
            "workbench_training: ingested %d record(s) for campaign %s (skipped %d dupes)",
            len(to_add),
            campaign_id,
            skipped,
        )
        if bias and bias.get("by_metric"):
            # Persist lightweight bias calibration to loop_history (no table)
            try:
                from datetime import datetime, timezone

                entry = {
                    "type": "prediction_bias",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "n_rows": bias.get("n_rows", len(to_add)),
                    "by_metric": bias["by_metric"],
                }
                store.append_loop_history_sync(campaign_id, entry)
            except Exception as exc:  # pragma: no cover
                logger.warning("prediction_bias history append failed: %s", exc)
    else:
        bias = {}

    msg = (
        f"已回灌 {len(to_add)} 条训练样本"
        if to_add
        else ("无新增 Completed 行" if not skipped else f"{skipped} 条已存在，跳过")
    )
    if bias and bias.get("by_metric"):
        # Append concise bias summary to message for API transparency
        parts = []
        for m, s in bias["by_metric"].items():
            parts.append(f"{m}: mean_err {s['mean_error']:+g} rmse {s['rmse']:g} (n={s['n']})")
        msg = f"{msg} | 预测偏差 " + "; ".join(parts)
        return {"ingested": len(to_add), "skipped": skipped, "message": msg, "prediction_bias": bias}
    return {"ingested": len(to_add), "skipped": skipped, "message": msg, "prediction_bias": bias if to_add else {}}


def resolve_experiment_for_row(campaign_id: int, row_id: int) -> int | None:
    """Resolve a workbench row to its ``ExperimentRow.id`` without side effects.

    Returns ``None`` when the row is not ingested yet (or does not exist), so a
    read-only caller (measurement listing, attachment listing) can degrade to an
    empty result rather than creating a placeholder.
    """
    from ..db.campaign_store import get_campaign_store
    from ..db.database import default_session_factory
    from ..db.models import ExperimentRow

    store = get_campaign_store()
    rows = store.list_rows_sync(campaign_id)
    match = next((r for r in rows if r.id == row_id), None)
    if match is None:
        return None
    label = workbench_record_label(campaign_id, match.item_id)
    with default_session_factory()() as session:
        row = (
            session.query(ExperimentRow).filter(ExperimentRow.label == label).first()
        )
        return row.id if row is not None else None


def ensure_experiment_for_row(campaign_id: int, row_id: int) -> int:
    """Resolve a workbench row to its ``ExperimentRow.id``, creating a placeholder if absent.

    The QC-report pipeline binds measurements to ``MeasurementRow.experiment_id``
    (FK → experiments.id), so a report can only attach once the row has an
    ExperimentRow. A row may not be ingested yet (Pending / no measured values);
    lazily create a placeholder stamped with the workbench label so the report
    binds immediately, and the next sync skips it (label already known).

    Raises ``ValueError`` when the row does not exist in the campaign.
    """
    from ..db.campaign_store import get_campaign_store
    from ..db.database import default_session_factory
    from ..db.models import ExperimentRow
    from ..db.session_utils import commit_session

    store = get_campaign_store()
    rows = store.list_rows_sync(campaign_id)
    match = next((r for r in rows if r.id == row_id), None)
    if match is None:
        raise ValueError(f"workbench row {row_id} not found in campaign {campaign_id}")

    label = workbench_record_label(campaign_id, match.item_id)
    with commit_session(default_session_factory()) as session:
        existing = (
            session.query(ExperimentRow).filter(ExperimentRow.label == label).first()
        )
        if existing is not None:
            return existing.id

        merged = {**(match.planned_params or {}), **(match.actual_params or {})}
        factors: dict[str, float] = {}
        cure_temp: float | None = None
        for key, val in merged.items():
            if key == "cure_temperature_c":
                try:
                    cure_temp = float(val)
                except (TypeError, ValueError):
                    pass
                continue
            try:
                factors[str(key)] = float(val)
            except (TypeError, ValueError):
                continue

        campaign = store.get_campaign_sync(campaign_id)
        domain = (
            _campaign_domain(campaign)
            if campaign is not None
            else ProductDomain.anticorrosion_coating
        )

        placeholder = ExperimentRow(
            item_id=None,
            domain=domain.value,
            project_id=(campaign.project_id or "") if campaign is not None else "",
            factors=factors,
            cure_temperature_c=cure_temp,
            measured=_numeric_measured(match.measurements or {}),
            source="workbench",
            label=label,
        )
        session.add(placeholder)
        session.refresh(placeholder)
        return placeholder.id
