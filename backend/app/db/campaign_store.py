"""Campaign workbench repository — Datalab Headless ELN (SSOT) with sqlite JSON fallback."""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..domain.objective_contract import (
    empty_measurements_template,
    normalize_objectives,
    objectives_from_snapshot,
    row_has_required_measurements,
    validate_measurements,
)
from ..domain.schemas import DOEPlan, ProductDomain, Requirement
from .campaign_types import WorkbenchRow
from .models import Campaign

logger = logging.getLogger(__name__)

_PARAMS_BLOCK = "formumind_params"
_MEASUREMENTS_BLOCK = "formumind_measurements"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_item_id(campaign_id: int, run_id: int) -> str:
    suffix = uuid.uuid4().hex[:8]
    return f"formumind_c{campaign_id}_r{run_id}_{suffix}"


def _blocks_for_row(
    *,
    planned_params: dict,
    actual_params: dict,
    measurements: dict,
    status: str,
) -> dict[str, Any]:
    return {
        _PARAMS_BLOCK: {
            "block_id": _PARAMS_BLOCK,
            "block_type": "generic",
            "data": {
                "planned_params": planned_params,
                "actual_params": actual_params,
                "status": status,
            },
        },
        _MEASUREMENTS_BLOCK: {
            "block_id": _MEASUREMENTS_BLOCK,
            "block_type": "generic",
            "data": dict(measurements),
        },
    }


def _parse_row_from_item(
    campaign_id: int,
    row_id: int,
    item_id: str,
    item_data: dict[str, Any],
) -> WorkbenchRow:
    blocks = item_data.get("blocks_obj") or {}
    params_block = (blocks.get(_PARAMS_BLOCK) or {}).get("data") or {}
    meas_block = (blocks.get(_MEASUREMENTS_BLOCK) or {}).get("data") or {}
    return WorkbenchRow(
        id=row_id,
        campaign_id=campaign_id,
        item_id=item_id,
        status=str(params_block.get("status") or "Pending"),
        planned_params=dict(params_block.get("planned_params") or {}),
        actual_params=dict(params_block.get("actual_params") or {}),
        measurements=dict(meas_block),
    )


class CampaignStoreInterface(ABC):
    """Abstract campaign + workbench row persistence (Datalab or local fallback)."""

    @abstractmethod
    async def create_from_plan(
        self,
        plan: DOEPlan,
        *,
        name: str | None = None,
        strategy: str = "BayBE-LHS",
        req: Requirement | None = None,
        project_id: str | None = None,
    ) -> Campaign: ...

    @abstractmethod
    async def get_campaign(self, campaign_id: int) -> Campaign | None: ...

    @abstractmethod
    async def list_rows(self, campaign_id: int) -> list[WorkbenchRow]: ...

    @abstractmethod
    async def batch_sync(
        self,
        campaign_id: int,
        rows: list[dict],
    ) -> tuple[int, list[WorkbenchRow]]: ...

    @abstractmethod
    async def get_experiments(self, campaign_id: int) -> list[WorkbenchRow]:
        """Completed rows with measurements — BayBE closed-loop input."""
        ...

    def list_rows_sync(self, campaign_id: int) -> list[WorkbenchRow]:
        import asyncio

        return asyncio.run(self.list_rows(campaign_id))

    def get_experiments_sync(self, campaign_id: int) -> list[WorkbenchRow]:
        import asyncio

        return asyncio.run(self.get_experiments(campaign_id))

    def get_campaign_sync(self, campaign_id: int) -> Campaign | None:
        import asyncio

        return asyncio.run(self.get_campaign(campaign_id))


class _CampaignMetaMixin:
    """Shared Campaign metadata writes (SQLite — not experiment measurements)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _create_campaign_meta(
        self,
        plan: DOEPlan,
        *,
        name: str | None,
        strategy: str,
        req: Requirement | None,
        project_id: str | None,
    ) -> Campaign:
        campaign_name = name or f"DOE {plan.design} ({plan.plan_id[:8] or 'local'})"
        domain = plan.domain or (req.domain if req else ProductDomain.anticorrosion_coating)
        objectives = normalize_objectives(req) if req else objectives_from_snapshot(None, domain)
        lever_snapshot = (
            [lev.model_dump() for lev in req.levers]
            if req and req.levers
            else [{"name": f.name, "low": f.low, "high": f.high, "unit": f.unit} for f in plan.factors]
        )
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
                sample_refs=[],
            )
            session.add(campaign)
            session.commit()
            session.refresh(campaign)
            return campaign

    def _get_campaign_sync(self, campaign_id: int) -> Campaign | None:
        with self._session_factory() as session:
            return session.get(Campaign, campaign_id)

    def _save_sample_refs(self, campaign_id: int, refs: list[dict]) -> None:
        with self._session_factory() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                return
            campaign.sample_refs = refs
            campaign.updated_at = _utcnow()
            session.commit()

    def _update_campaign_status(self, campaign_id: int, rows: list[WorkbenchRow]) -> None:
        with self._session_factory() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                return
            completed = sum(1 for r in rows if r.status == "Completed")
            total = len(rows)
            campaign.status = "COMPLETED" if total > 0 and completed == total else "IN_PROGRESS"
            campaign.updated_at = _utcnow()
            session.commit()


class DatalabCampaignStore(_CampaignMetaMixin, CampaignStoreInterface):
    """Async httpx proxy to Datalab Headless ELN (SSOT for workbench rows)."""

    def __init__(
        self,
        api_url: str,
        session_factory: sessionmaker[Session],
        *,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(session_factory)
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    async def _create_sample(self, sample_data: dict[str, Any]) -> dict[str, Any]:
        payload = {"new_sample_data": sample_data, "generate_id_automatically": False}
        logger.info("Datalab POST /new-sample/ payload=%s", payload)
        async with httpx.AsyncClient(base_url=self._api_url, timeout=self._timeout) as client:
            resp = await client.post("/new-sample/", json=payload)
            resp.raise_for_status()
            body = resp.json()
        entry = body.get("sample_list_entry") or body
        logger.info("Datalab created sample item_id=%s", entry.get("item_id") or sample_data.get("item_id"))
        return entry

    async def _get_item(self, item_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self._api_url, timeout=self._timeout) as client:
            resp = await client.get(f"/get-item-data/{item_id}")
            resp.raise_for_status()
            body = resp.json()
        item_data = body.get("item_data") if isinstance(body.get("item_data"), dict) else body
        return item_data

    async def _save_item(self, item_id: str, item_data: dict[str, Any]) -> dict[str, Any]:
        payload = {"item_id": item_id, "data": item_data}
        logger.info("Datalab POST /save-item/ item_id=%s", item_id)
        async with httpx.AsyncClient(base_url=self._api_url, timeout=self._timeout) as client:
            resp = await client.post("/save-item/", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def create_from_plan(
        self,
        plan: DOEPlan,
        *,
        name: str | None = None,
        strategy: str = "BayBE-LHS",
        req: Requirement | None = None,
        project_id: str | None = None,
    ) -> Campaign:
        campaign = self._create_campaign_meta(
            plan, name=name, strategy=strategy, req=req, project_id=project_id
        )
        domain = plan.domain or (req.domain if req else ProductDomain.anticorrosion_coating)
        objectives = normalize_objectives(req) if req else objectives_from_snapshot(None, domain)
        meas_template = empty_measurements_template(objectives)
        refs: list[dict] = []

        for idx, run in enumerate(plan.runs, start=1):
            item_id = _new_item_id(campaign.id, run.run_id or idx)
            planned = dict(run.natural)
            blocks = _blocks_for_row(
                planned_params=planned,
                actual_params=dict(planned),
                measurements=dict(meas_template),
                status="Pending",
            )
            sample_data = {
                "item_id": item_id,
                "name": f"{campaign.name} — run {idx}",
                "description": f"FormuMind DOE run {run.run_id}",
                "type": ["samples"],
                "blocks_obj": blocks,
                "display_order": [_PARAMS_BLOCK, _MEASUREMENTS_BLOCK],
            }
            await self._create_sample(sample_data)
            refs.append({"id": idx, "item_id": item_id})

        self._save_sample_refs(campaign.id, refs)
        campaign.sample_refs = refs
        return campaign

    async def get_campaign(self, campaign_id: int) -> Campaign | None:
        return self._get_campaign_sync(campaign_id)

    async def list_rows(self, campaign_id: int) -> list[WorkbenchRow]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return []
        out: list[WorkbenchRow] = []
        for ref in campaign.sample_refs or []:
            row_id = int(ref["id"])
            item_id = str(ref["item_id"])
            try:
                item_data = await self._get_item(item_id)
                out.append(_parse_row_from_item(campaign_id, row_id, item_id, item_data))
            except Exception as exc:
                logger.warning("Datalab get-item failed for %s: %s", item_id, exc)
        return out

    async def batch_sync(
        self,
        campaign_id: int,
        rows: list[dict],
    ) -> tuple[int, list[WorkbenchRow]]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return 0, []

        domain = ProductDomain.anticorrosion_coating
        objectives = objectives_from_snapshot(campaign.objectives_snapshot, domain)
        ref_by_id = {int(r["id"]): str(r["item_id"]) for r in (campaign.sample_refs or [])}
        updated = 0

        for payload in rows:
            row_id = int(payload["id"])
            item_id = ref_by_id.get(row_id)
            if not item_id:
                continue
            try:
                item_data = await self._get_item(item_id)
            except Exception as exc:
                logger.warning("batch_sync skip %s: %s", item_id, exc)
                continue

            params_block = ((item_data.get("blocks_obj") or {}).get(_PARAMS_BLOCK) or {}).get("data") or {}
            planned = dict(params_block.get("planned_params") or {})
            actual = dict(payload.get("actual_params") or {})
            raw_meas = payload.get("measurements") or {}
            try:
                measurements = validate_measurements(raw_meas, objectives, strict=True)
            except ValueError:
                measurements = validate_measurements(raw_meas, objectives)
            status = payload.get("status") or params_block.get("status") or "Pending"
            if row_has_required_measurements(measurements, objectives):
                status = "Completed"

            blocks = _blocks_for_row(
                planned_params=planned,
                actual_params=actual,
                measurements=measurements,
                status=status,
            )
            item_data["blocks_obj"] = blocks
            item_data["display_order"] = [_PARAMS_BLOCK, _MEASUREMENTS_BLOCK]
            await self._save_item(item_id, item_data)
            updated += 1

        refreshed = await self.list_rows(campaign_id)
        self._update_campaign_status(campaign_id, refreshed)
        return updated, refreshed

    async def get_experiments(self, campaign_id: int) -> list[WorkbenchRow]:
        rows = await self.list_rows(campaign_id)
        return [r for r in rows if r.status == "Completed"]


class SqliteCampaignStore(_CampaignMetaMixin, CampaignStoreInterface):
    """Local JSON-in-Campaign fallback when Datalab is unreachable (tests / offline)."""

    async def create_from_plan(
        self,
        plan: DOEPlan,
        *,
        name: str | None = None,
        strategy: str = "BayBE-LHS",
        req: Requirement | None = None,
        project_id: str | None = None,
    ) -> Campaign:
        campaign = self._create_campaign_meta(
            plan, name=name, strategy=strategy, req=req, project_id=project_id
        )
        domain = plan.domain or (req.domain if req else ProductDomain.anticorrosion_coating)
        objectives = normalize_objectives(req) if req else objectives_from_snapshot(None, domain)
        meas_template = empty_measurements_template(objectives)
        refs: list[dict] = []
        for idx, run in enumerate(plan.runs, start=1):
            planned = dict(run.natural)
            item_id = f"local_c{campaign.id}_r{idx}"
            refs.append(
                {
                    "id": idx,
                    "item_id": item_id,
                    "status": "Pending",
                    "planned_params": planned,
                    "actual_params": dict(planned),
                    "measurements": dict(meas_template),
                }
            )
        self._save_sample_refs(campaign.id, refs)
        campaign.sample_refs = refs
        return campaign

    async def get_campaign(self, campaign_id: int) -> Campaign | None:
        return self._get_campaign_sync(campaign_id)

    def _refs_to_rows(self, campaign: Campaign) -> list[WorkbenchRow]:
        return [
            WorkbenchRow(
                id=int(ref["id"]),
                campaign_id=campaign.id,
                item_id=str(ref.get("item_id") or f"local_{ref['id']}"),
                status=str(ref.get("status") or "Pending"),
                planned_params=dict(ref.get("planned_params") or {}),
                actual_params=dict(ref.get("actual_params") or {}),
                measurements=dict(ref.get("measurements") or {}),
            )
            for ref in (campaign.sample_refs or [])
        ]

    async def list_rows(self, campaign_id: int) -> list[WorkbenchRow]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return []
        return self._refs_to_rows(campaign)

    async def batch_sync(
        self,
        campaign_id: int,
        rows: list[dict],
    ) -> tuple[int, list[WorkbenchRow]]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return 0, []

        domain = ProductDomain.anticorrosion_coating
        objectives = objectives_from_snapshot(campaign.objectives_snapshot, domain)
        refs = list(campaign.sample_refs or [])
        ref_by_id = {int(r["id"]): r for r in refs}
        updated = 0

        for payload in rows:
            row_id = int(payload["id"])
            ref = ref_by_id.get(row_id)
            if ref is None:
                continue
            ref["actual_params"] = payload.get("actual_params") or {}
            raw_meas = payload.get("measurements") or {}
            try:
                ref["measurements"] = validate_measurements(raw_meas, objectives, strict=True)
            except ValueError:
                ref["measurements"] = validate_measurements(raw_meas, objectives)
            status = payload.get("status") or ref.get("status") or "Pending"
            if row_has_required_measurements(ref["measurements"], objectives):
                status = "Completed"
            ref["status"] = status
            updated += 1

        self._save_sample_refs(campaign_id, refs)
        refreshed = self._refs_to_rows(campaign)
        self._update_campaign_status(campaign_id, refreshed)
        return updated, refreshed

    async def get_experiments(self, campaign_id: int) -> list[WorkbenchRow]:
        rows = await self.list_rows(campaign_id)
        return [r for r in rows if r.status == "Completed"]


_store: CampaignStoreInterface | None = None


def get_campaign_store(settings: Settings | None = None) -> CampaignStoreInterface:
    global _store
    if _store is not None:
        return _store
    s = settings or get_settings()
    from .database import default_session_factory

    factory = default_session_factory()
    if s.campaign_backend == "datalab":
        _store = DatalabCampaignStore(s.datalab_api_url, factory, timeout=s.datalab_timeout_seconds)
    else:
        _store = SqliteCampaignStore(factory)
    return _store


def reset_campaign_store(store: CampaignStoreInterface | None = None) -> None:
    """Test helper — inject a store or clear the singleton."""
    global _store
    _store = store
