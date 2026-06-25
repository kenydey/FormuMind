"""SQLite-backed DOE workbench (Campaign + ExperimentRecord rows)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from ..domain.objective_contract import (
    empty_measurements_template,
    normalize_objectives,
    objectives_from_snapshot,
    row_has_required_measurements,
    validate_measurements,
)
from ..domain.schemas import DOEPlan, ProductDomain, Requirement
from .models import Campaign, ExperimentRecord


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CampaignStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_from_plan(
        self,
        plan: DOEPlan,
        *,
        name: str | None = None,
        strategy: str = "BayBE-LHS",
        req: Requirement | None = None,
        project_id: str | None = None,
    ) -> Campaign:
        campaign_name = name or f"DOE {plan.design} ({plan.plan_id[:8] or 'local'})"
        domain = plan.domain or (req.domain if req else ProductDomain.anticorrosion_coating)
        objectives = normalize_objectives(req) if req else objectives_from_snapshot(None, domain)
        lever_snapshot = (
            [lev.model_dump() for lev in req.levers]
            if req and req.levers
            else [{"name": f.name, "low": f.low, "high": f.high, "unit": f.unit} for f in plan.factors]
        )
        meas_template = empty_measurements_template(objectives)
        primary = objectives[0].metric if objectives else None

        with self._session_factory() as session:
            campaign = Campaign(
                name=campaign_name,
                strategy=strategy,
                status="IN_PROGRESS",
                project_id=project_id,
                primary_metric=primary,
                objectives_snapshot=[o.model_dump() for o in objectives],
                lever_snapshot=lever_snapshot,
            )
            session.add(campaign)
            session.flush()
            for run in plan.runs:
                session.add(
                    ExperimentRecord(
                        campaign_id=campaign.id,
                        status="Pending",
                        planned_params=dict(run.natural),
                        actual_params=dict(run.natural),
                        measurements=dict(meas_template),
                    )
                )
            session.commit()
            session.refresh(campaign)
            return campaign

    def get_campaign(self, campaign_id: int) -> Campaign | None:
        with self._session_factory() as session:
            return session.get(Campaign, campaign_id)

    def list_rows(self, campaign_id: int) -> list[ExperimentRecord]:
        with self._session_factory() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                return []
            return list(
                session.query(ExperimentRecord)
                .filter(ExperimentRecord.campaign_id == campaign_id)
                .order_by(ExperimentRecord.id)
                .all()
            )

    def batch_sync(
        self,
        campaign_id: int,
        rows: list[dict],
    ) -> tuple[int, list[ExperimentRecord]]:
        """Apply grid updates; auto-complete rows with filled primary objective."""
        updated = 0
        with self._session_factory() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                return 0, []

            domain = ProductDomain.anticorrosion_coating
            objectives = objectives_from_snapshot(campaign.objectives_snapshot, domain)

            for payload in rows:
                row = session.get(ExperimentRecord, payload["id"])
                if row is None or row.campaign_id != campaign_id:
                    continue
                row.actual_params = payload.get("actual_params") or {}
                raw_meas = payload.get("measurements") or {}
                try:
                    row.measurements = validate_measurements(raw_meas, objectives, strict=True)
                except ValueError:
                    row.measurements = validate_measurements(raw_meas, objectives)
                status = payload.get("status") or row.status
                if row_has_required_measurements(row.measurements, objectives):
                    status = "Completed"
                row.status = status
                row.updated_at = _utcnow()
                updated += 1

            completed = (
                session.query(ExperimentRecord)
                .filter(
                    ExperimentRecord.campaign_id == campaign_id,
                    ExperimentRecord.status == "Completed",
                )
                .count()
            )
            total = (
                session.query(ExperimentRecord)
                .filter(ExperimentRecord.campaign_id == campaign_id)
                .count()
            )
            if total > 0 and completed == total:
                campaign.status = "COMPLETED"
            else:
                campaign.status = "IN_PROGRESS"
            campaign.updated_at = _utcnow()
            session.commit()

            refreshed = (
                session.query(ExperimentRecord)
                .filter(ExperimentRecord.campaign_id == campaign_id)
                .order_by(ExperimentRecord.id)
                .all()
            )
            return updated, list(refreshed)


_store: CampaignStore | None = None


def get_campaign_store() -> CampaignStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = CampaignStore(default_session_factory())
    return _store
