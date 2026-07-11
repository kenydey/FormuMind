"""Campaign workbench repository — Datalab Headless ELN (SSOT) with sqlite JSON fallback."""
from __future__ import annotations

from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import asyncio
import concurrent.futures
import logging
import threading
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
from ..domain.schemas import (
    DOEPlan,
    DatalabSampleResponse,
    ProductDomain,
    Requirement,
)
from .campaign_types import WorkbenchRow
from .datalab_client import (
    DatalabStoreError,
    DatalabUnavailableError,
    check_datalab_reachable,
    datalab_block,
    datalab_sample_type,
    parse_create_sample_response,
    parse_delete_response,
    parse_item_envelope,
    validate_blocks,
)
from .session_utils import commit_session
from .models import Campaign

logger = logging.getLogger(__name__)

_PARAMS_BLOCK = "formumind_params"
_MEASUREMENTS_BLOCK = "formumind_measurements"
_CAMPAIGN_BLOCKS = (_PARAMS_BLOCK, _MEASUREMENTS_BLOCK)


def _campaign_domain(campaign: Campaign) -> ProductDomain:
    """Resolve the campaign's product domain (legacy rows may lack the column)."""
    raw = getattr(campaign, "domain", None)
    if raw:
        try:
            return ProductDomain(raw)
        except ValueError:
            logger.warning("Campaign %s has unknown domain %r", campaign.id, raw)
    return ProductDomain.anticorrosion_coating


def _run_async(coro):
    """Run async store methods from sync callers without nesting event loops."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def _validate_campaign_blocks(item_data: dict[str, Any]) -> None:
    validate_blocks(item_data, _CAMPAIGN_BLOCKS)


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
        _PARAMS_BLOCK: datalab_block(
            _PARAMS_BLOCK,
            {
                "planned_params": planned_params,
                "actual_params": actual_params,
                "status": status,
            },
        ),
        _MEASUREMENTS_BLOCK: datalab_block(_MEASUREMENTS_BLOCK, dict(measurements)),
    }


def _parse_row_from_item(
    campaign_id: int,
    row_id: int,
    item_id: str,
    item_data: dict[str, Any],
) -> WorkbenchRow:
    _validate_campaign_blocks(item_data)
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
        return _run_async(self.list_rows(campaign_id))

    def get_experiments_sync(self, campaign_id: int) -> list[WorkbenchRow]:
        return _run_async(self.get_experiments(campaign_id))

    def get_campaign_sync(self, campaign_id: int) -> Campaign | None:
        return _run_async(self.get_campaign(campaign_id))

    async def close(self) -> None:
        """Release external resources (no-op for sqlite fallback)."""
        return None


class _CampaignMetaMixin:
    """Shared Campaign metadata writes (SQLite — not experiment measurements)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._write_lock = threading.RLock()

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
        from ..domain.project_spec import lever_snapshot_from_plan

        lever_snapshot = lever_snapshot_from_plan(plan, req)
        primary = objectives[0].metric if objectives else None
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = Campaign(
                    name=campaign_name,
                    strategy=strategy,
                    status="IN_PROGRESS",
                    domain=domain.value,
                    project_id=project_id,
                    primary_metric=primary,
                    objectives_snapshot=[o.model_dump() for o in objectives],
                    lever_snapshot=lever_snapshot,
                    sample_refs=[],
                )
                session.add(campaign)
                session.flush()
                session.refresh(campaign)
                return campaign

    def _get_campaign_sync(self, campaign_id: int) -> Campaign | None:
        with self._session_factory() as session:
            return session.get(Campaign, campaign_id)

    def _save_sample_refs(self, campaign_id: int, refs: list[dict]) -> None:
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    return
                campaign.sample_refs = refs
                campaign.updated_at = _utcnow()

    def _delete_campaign_meta(self, campaign_id: int) -> None:
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    return
                session.delete(campaign)

    def _update_campaign_status(self, campaign_id: int, rows: list[WorkbenchRow]) -> None:
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    return
                completed = sum(1 for r in rows if r.status == "Completed")
                total = len(rows)
                campaign.status = "COMPLETED" if total > 0 and completed == total else "IN_PROGRESS"
                campaign.updated_at = _utcnow()


class DatalabCampaignStore(_CampaignMetaMixin, CampaignStoreInterface):
    """Async httpx proxy to Datalab Headless ELN (SSOT for workbench rows)."""

    def __init__(
        self,
        api_url: str,
        session_factory: sessionmaker[Session],
        *,
        timeout: float = 30.0,
        max_connections: int = 10,
        max_keepalive_connections: int = 5,
    ) -> None:
        super().__init__(session_factory)
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._api_url,
                timeout=self._timeout,
                limits=self._limits,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _create_sample(self, sample_data: dict[str, Any]) -> DatalabSampleResponse:
        expected_id = str(sample_data["item_id"])
        payload = {"new_sample_data": sample_data, "generate_id_automatically": False}
        logger.info("Datalab POST /new-sample/ item_id=%s", expected_id)
        client = await self._ensure_client()
        resp = await client.post("/new-sample/", json=payload)
        resp.raise_for_status()
        sample = parse_create_sample_response(resp.json(), expected_id)
        logger.info("Datalab created sample item_id=%s", sample.item_id)
        return sample

    async def _get_item(self, item_id: str) -> dict[str, Any]:
        client = await self._ensure_client()
        resp = await client.get(f"/get-item-data/{item_id}")
        resp.raise_for_status()
        return parse_item_envelope(resp.json(), validate=_validate_campaign_blocks)

    async def _save_item(self, item_id: str, item_data: dict[str, Any]) -> dict[str, Any]:
        _validate_campaign_blocks(item_data)
        payload = {"item_id": item_id, "data": item_data}
        logger.info("Datalab POST /save-item/ item_id=%s", item_id)
        client = await self._ensure_client()
        resp = await client.post("/save-item/", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def _delete_sample(self, item_id: str) -> None:
        client = await self._ensure_client()
        resp = await client.post("/delete-sample/", json={"item_id": item_id})
        resp.raise_for_status()
        parse_delete_response(resp.json(), item_id)

    async def _rollback_created_samples(self, item_ids: list[str]) -> None:
        for item_id in reversed(item_ids):
            try:
                await self._delete_sample(item_id)
                logger.info("Saga rollback: deleted sample %s", item_id)
            except Exception as exc:
                logger.error("Saga rollback failed for %s: %s", item_id, exc)

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
        created_item_ids: list[str] = []
        refs: list[dict] = []

        try:
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
                    "type": datalab_sample_type(),
                    "blocks_obj": blocks,
                    "display_order": [_PARAMS_BLOCK, _MEASUREMENTS_BLOCK],
                }
                created_item_ids.append(item_id)
                await self._create_sample(sample_data)
                refs.append({"id": idx, "item_id": item_id})

            self._save_sample_refs(campaign.id, refs)
            campaign.sample_refs = refs
            return campaign

        except Exception as exc:
            logger.error(
                "create_from_plan failed after %d/%d samples: %s",
                len(created_item_ids),
                len(plan.runs),
                exc,
            )
            await self._rollback_created_samples(created_item_ids)
            self._delete_campaign_meta(campaign.id)
            raise DatalabUnavailableError(self._api_url, str(exc)) from exc

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
            item_data = await self._get_item(item_id)
            out.append(_parse_row_from_item(campaign_id, row_id, item_id, item_data))
        return out

    async def batch_sync(
        self,
        campaign_id: int,
        rows: list[dict],
    ) -> tuple[int, list[WorkbenchRow]]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return 0, []

        domain = _campaign_domain(campaign)
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
            try:
                await self._save_item(item_id, item_data)
            except Exception as exc:
                logger.warning("batch_sync save failed for %s: %s", item_id, exc)
                continue
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

        domain = _campaign_domain(campaign)
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

    def list_rows_sync(self, campaign_id: int) -> list[WorkbenchRow]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return []
        return self._refs_to_rows(campaign)

    def get_experiments_sync(self, campaign_id: int) -> list[WorkbenchRow]:
        return [r for r in self.list_rows_sync(campaign_id) if r.status == "Completed"]

    def get_campaign_sync(self, campaign_id: int) -> Campaign | None:
        return self._get_campaign_sync(campaign_id)


_store: CampaignStoreInterface | None = None


def _datalab_required(settings: Settings) -> bool:
    if settings.datalab_required:
        return True
    return settings.campaign_backend.lower() == "datalab" or settings.experiment_backend.lower() == "datalab"


def _ensure_datalab_or_raise(settings: Settings) -> None:
    ok, reason = check_datalab_reachable(
        settings.datalab_api_url,
        timeout=min(2.0, settings.datalab_timeout_seconds),
    )
    if not ok:
        raise DatalabUnavailableError(settings.datalab_api_url, reason)


def get_campaign_store(settings: Settings | None = None) -> CampaignStoreInterface:
    global _store
    if _store is not None:
        return _store
    s = settings or get_settings()
    from .database import default_session_factory

    factory = default_session_factory()
    backend = (s.campaign_backend or "sqlite").lower()

    if backend == "datalab" or (backend == "auto" and _datalab_required(s)):
        _ensure_datalab_or_raise(s)
        _store = DatalabCampaignStore(
            s.datalab_api_url,
            factory,
            timeout=s.datalab_timeout_seconds,
            max_connections=s.datalab_max_connections,
            max_keepalive_connections=s.datalab_max_keepalive_connections,
        )
        logger.info("Campaign store: Datalab SSOT (%s)", s.datalab_api_url)
        return _store

    if backend == "auto":
        ok, _ = check_datalab_reachable(
            s.datalab_api_url,
            timeout=min(2.0, s.datalab_timeout_seconds),
        )
        if ok:
            _store = DatalabCampaignStore(
                s.datalab_api_url,
                factory,
                timeout=s.datalab_timeout_seconds,
                max_connections=s.datalab_max_connections,
                max_keepalive_connections=s.datalab_max_keepalive_connections,
            )
            logger.info("Campaign store: Datalab (auto, %s)", s.datalab_api_url)
            return _store
        logger.warning(
            "Campaign store: sqlite dev fallback (Datalab unreachable at %s)",
            s.datalab_api_url,
        )
        _store = SqliteCampaignStore(factory)
        return _store

    if s.environment == "production":
        logger.warning(
            "SqliteCampaignStore is deprecated for production; set FORMUMIND_CAMPAIGN_BACKEND=datalab"
        )
    _store = SqliteCampaignStore(factory)
    return _store


def reset_campaign_store(store: CampaignStoreInterface | None = None) -> None:
    """Test helper — inject a store or clear the singleton."""
    global _store
    _store = store
