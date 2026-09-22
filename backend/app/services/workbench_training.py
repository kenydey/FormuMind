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


def persist_workbench_lab_ledger(
    campaign_id: int,
    rows: list[WorkbenchRow] | None = None,
) -> dict:
    """Upsert Completed workbench measurements into SQL ``ExperimentRow`` for dossier S5.

    Independent of the training registry / Datalab reachability: Hub 卷宗
    ``_lab_slice`` reads ``ExperimentRow`` by ``project_id``. When the registry
    falls back to an empty JSON store (ELN down), this path still fills S5.
    Idempotent on ``wb:{campaign}:{item_id}`` labels.
    """
    from ..db.campaign_store import get_campaign_store
    from ..db.database import default_session_factory
    from ..db.models import ExperimentRow
    from ..db.session_utils import commit_session

    store = get_campaign_store()
    campaign = store.get_campaign_sync(campaign_id)
    if campaign is None:
        return {"ok": False, "upserted": 0, "reason": "campaign_not_found"}

    project_id = (campaign.project_id or "").strip()
    domain = _campaign_domain(campaign)
    wb_rows = list(rows) if rows is not None else store.get_experiments_sync(campaign_id)

    upserted = 0
    skipped = 0
    with commit_session(default_session_factory()) as session:
        for row in wb_rows:
            rec = row_to_experiment_record(
                row,
                campaign_id=campaign_id,
                domain=domain,
                project_id=project_id,
            )
            if rec is None:
                skipped += 1
                continue
            existing = (
                session.query(ExperimentRow)
                .filter(ExperimentRow.label == rec.label)
                .first()
            )
            if existing is not None:
                existing.measured = dict(rec.measured)
                existing.factors = dict(rec.factors)
                existing.cure_temperature_c = rec.cure_temperature_c
                existing.source = "workbench"
                if project_id and not (existing.project_id or "").strip():
                    existing.project_id = project_id
                elif project_id:
                    existing.project_id = project_id
                upserted += 1
                continue
            session.add(
                ExperimentRow(
                    item_id=None,
                    domain=rec.domain.value,
                    project_id=project_id,
                    factors=dict(rec.factors),
                    cure_temperature_c=rec.cure_temperature_c,
                    measured=dict(rec.measured),
                    source="workbench",
                    label=rec.label,
                )
            )
            upserted += 1

    if project_id and upserted:
        _link_campaign_to_project(project_id, campaign_id)

    logger.info(
        "workbench_lab_ledger: campaign=%s project=%s upserted=%d skipped=%d",
        campaign_id,
        project_id or "-",
        upserted,
        skipped,
    )
    return {
        "ok": True,
        "upserted": upserted,
        "skipped": skipped,
        "project_id": project_id,
        "campaign_id": campaign_id,
    }


def _link_campaign_to_project(project_id: str, campaign_id: int) -> None:
    """Best-effort: stamp ``workspace.workbench_campaign_id`` for dossier hydrate."""
    try:
        from ..db.project_store import get_project_store

        projects = get_project_store()
        detail = projects.get(project_id)
        if detail is None:
            return
        if detail.workspace.workbench_campaign_id == campaign_id:
            return
        projects.update(project_id, {"workbench_campaign_id": campaign_id})
    except Exception as exc:  # pragma: no cover
        logger.debug("link campaign→project failed: %s", exc)


def _numeric_measured(measurements: dict, report: list[str] | None = None) -> dict[str, float]:
    """Coerce measurements to float, dropping non-numeric / empty values.

    Each dropped value is logged and, when ``report`` is supplied, appended as
    ``"non_numeric:<key>"`` so the caller can surface data-quality losses.
    """
    out: dict[str, float] = {}
    for key, val in (measurements or {}).items():
        if val is None or val == "":
            if report is not None:
                report.append(f"empty:{key}")
            continue
        try:
            out[str(key)] = float(val)
        except (TypeError, ValueError):
            if report is not None:
                report.append(f"non_numeric:{key}")
            logger.warning(
                "workbench_training: dropped non-numeric measurement %s=%r",
                key,
                val,
            )
            continue
    return out


def row_to_experiment_record(
    row: WorkbenchRow,
    *,
    campaign_id: int,
    domain: ProductDomain,
    project_id: str = "",
    report: list[str] | None = None,
) -> ExperimentRecord | None:
    if row.status != "Completed":
        return None
    measured = _numeric_measured(row.measurements, report=report)
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
            if report is not None:
                report.append(f"non_numeric_factor:{key}")
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
    """Idempotently push Completed workbench rows into ModelRegistry.

    Always persists a SQL lab-ledger mirror for dossier S5 first (even when
    ``workbench_auto_train`` is off or the training store is degraded).
    """
    settings = get_settings()
    ledger = persist_workbench_lab_ledger(campaign_id, rows)
    if not settings.workbench_auto_train:
        return {
            "ingested": 0,
            "skipped": 0,
            "message": "workbench_auto_train disabled",
            "lab_ledger": ledger,
        }

    from ..db.campaign_store import get_campaign_store
    from .training import registry

    store = get_campaign_store()
    campaign = store.get_campaign_sync(campaign_id)
    if campaign is None:
        return {
            "ingested": 0,
            "skipped": 0,
            "message": "campaign not found",
            "lab_ledger": ledger,
        }

    domain = _campaign_domain(campaign)
    project_id = (campaign.project_id or "").strip()
    known = registry.known_labels()

    to_add: list[ExperimentRecord] = []
    skipped = 0
    report: list[str] = []
    for row in rows:
        rec = row_to_experiment_record(
            row, campaign_id=campaign_id, domain=domain, project_id=project_id, report=report
        )
        if rec is None:
            continue
        if rec.label in known:
            skipped += 1
            continue
        to_add.append(rec)
        known.add(rec.label)

    quality: dict = {"dropped_values": len(report), "dropped": report[:20]}
    if report:
        logger.warning(
            "workbench_training: campaign %s dropped %d invalid value(s): %s",
            campaign_id,
            len(report),
            ", ".join(report[:20]),
        )
        try:
            from datetime import datetime, timezone

            store.append_loop_history_sync(
                campaign_id,
                {
                    "type": "data_quality",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "dropped_values": len(report),
                    "dropped": report[:20],
                },
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("data_quality history append failed: %s", exc)

    if to_add:
        # P2: compute bias BEFORE retrain (use current model as baseline)
        bias = _compute_prediction_bias(to_add, domain, project_id)
        try:
            registry.add(to_add, retrain=retrain)
        except Exception as exc:
            # Lab ledger already persisted; training store (Datalab) may be down.
            logger.warning(
                "workbench_training: registry.add failed after lab ledger "
                "(campaign=%s upserted=%s): %s",
                campaign_id,
                ledger.get("upserted"),
                exc,
            )
            return {
                "ingested": 0,
                "skipped": skipped,
                "message": (
                    f"台账已写入卷宗镜像 {ledger.get('upserted') or 0} 条；"
                    f"训练库写入失败：{exc}"
                ),
                "prediction_bias": bias if bias else {},
                "quality": quality,
                "lab_ledger": ledger,
                "training_error": str(exc),
            }
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
        return {
            "ingested": len(to_add),
            "skipped": skipped,
            "message": msg,
            "prediction_bias": bias,
            "quality": quality,
            "lab_ledger": ledger,
        }
    return {
        "ingested": len(to_add),
        "skipped": skipped,
        "message": msg,
        "prediction_bias": bias if to_add else {},
        "quality": quality,
        "lab_ledger": ledger,
    }


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
        session.flush()  # 先落库拿 autoincrement id，refresh 才有效（commit 在 with 退出时）
        session.refresh(placeholder)
        return placeholder.id
