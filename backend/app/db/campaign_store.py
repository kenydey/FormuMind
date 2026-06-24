"""SQLite-backed DOE workbench (Campaign + ExperimentRecord rows)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from ..domain.schemas import DOEPlan
from .models import Campaign, ExperimentRecord


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_has_measurements(measurements: dict) -> bool:
    if not measurements:
        return False
    return any(v is not None and v != "" for v in measurements.values())


class CampaignStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_from_plan(
        self,
        plan: DOEPlan,
        *,
        name: str | None = None,
        strategy: str = "BayBE-LHS",
    ) -> Campaign:
        campaign_name = name or f"DOE {plan.design} ({plan.plan_id[:8] or 'local'})"
        with self._session_factory() as session:
            campaign = Campaign(name=campaign_name, strategy=strategy, status="IN_PROGRESS")
            session.add(campaign)
            session.flush()
            for run in plan.runs:
                session.add(
                    ExperimentRecord(
                        campaign_id=campaign.id,
                        status="Pending",
                        planned_params=dict(run.natural),
                        actual_params=dict(run.natural),
                        measurements={},
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
        """Apply grid updates; auto-complete rows with filled measurements."""
        updated = 0
        with self._session_factory() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                return 0, []

            for payload in rows:
                row = session.get(ExperimentRecord, payload["id"])
                if row is None or row.campaign_id != campaign_id:
                    continue
                row.actual_params = payload.get("actual_params") or {}
                row.measurements = payload.get("measurements") or {}
                status = payload.get("status") or row.status
                if _row_has_measurements(row.measurements):
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
