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


def ingest_workbench_rows(
    campaign_id: int,
    rows: list[WorkbenchRow],
    *,
    retrain: bool = True,
) -> dict[str, int | str]:
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
        registry.add(to_add, retrain=retrain)
        logger.info(
            "workbench_training: ingested %d record(s) for campaign %s (skipped %d dupes)",
            len(to_add),
            campaign_id,
            skipped,
        )

    msg = (
        f"已回灌 {len(to_add)} 条训练样本"
        if to_add
        else ("无新增 Completed 行" if not skipped else f"{skipped} 条已存在，跳过")
    )
    return {"ingested": len(to_add), "skipped": skipped, "message": msg}


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

    store = get_campaign_store()
    rows = store.list_rows_sync(campaign_id)
    match = next((r for r in rows if r.id == row_id), None)
    if match is None:
        raise ValueError(f"workbench row {row_id} not found in campaign {campaign_id}")

    label = workbench_record_label(campaign_id, match.item_id)
    with default_session_factory()() as session:
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
        session.commit()
        session.refresh(placeholder)
        return placeholder.id
