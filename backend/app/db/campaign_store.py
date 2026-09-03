"""Campaign workbench repository — Datalab Headless ELN (SSOT) with sqlite JSON fallback."""
from __future__ import annotations

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
    DatalabUnavailableError,
    check_datalab_reachable,
    datalab_block,
    datalab_headers,
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
    note: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        _PARAMS_BLOCK: datalab_block(
            _PARAMS_BLOCK,
            {
                "planned_params": planned_params,
                "actual_params": actual_params,
                "status": status,
                "note": note,
                "tags": list(tags or []),
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
        note=params_block.get("note"),
        tags=list(params_block.get("tags") or []),
        refcode=item_data.get("refcode"),
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
        owner_id: str | None = None,
    ) -> Campaign: ...

    @abstractmethod
    async def get_campaign(self, campaign_id: int) -> Campaign | None: ...

    @abstractmethod
    async def list_rows(self, campaign_id: int) -> list[WorkbenchRow]: ...

    async def set_row_tags(
        self, campaign_id: int, row_id: int, tags: list[str]
    ) -> WorkbenchRow | None:
        """只更新单行 tags（默认实现走 batch_sync，子类可覆盖为字段级更新）。"""
        return None

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

    async def reconcile_sample_refs(self, campaign_id: int) -> dict:
        """Detect and prune stale ``sample_refs`` whose Datalab item is gone.

        Returns ``{removed, kept, removed_count, errors}``. Network / 5xx
        failures are reported in ``errors`` and never pruned (only a definitive
        404 is treated as deleted). Idempotent.
        """
        return {"removed": [], "kept": [], "removed_count": 0, "errors": []}

    async def probe_sample_refs(self, campaign_id: int) -> dict:
        """Read-only stale-ref probe (no pruning) for the quality report.

        Returns ``{stale, valid, errors}`` where ``stale`` are definitive 404s
        and ``errors`` are transient failures (neither is modified).
        """
        return {"stale": [], "valid": [], "errors": []}

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
        owner_id: str | None = None,
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
                    project_id=project_id,
                    owner_id=owner_id,
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

    def _append_loop_history(self, campaign_id: int, entry: dict) -> None:
        with self._write_lock:
            with commit_session(self._session_factory) as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    return
                history = list(campaign.loop_history or [])
                entry = dict(entry)
                entry["round"] = len(history) + 1
                history.append(entry)
                campaign.loop_history = history
                campaign.updated_at = _utcnow()

    def append_loop_history_sync(self, campaign_id: int, entry: dict) -> None:
        self._append_loop_history(campaign_id, entry)

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
        self._client_loop: asyncio.AbstractEventLoop | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无运行事件循环（同步/线程上下文）：回退到默认策略 loop，
            # 避免 get_running_loop 抛 RuntimeError 使整个 store 不可用（C17 加固）。
            loop = asyncio.new_event_loop()
        if self._client is not None and self._client_loop is None:
            # Client injected externally (e.g. tests) — bind to current loop.
            self._client_loop = loop
        if (
            self._client is None
            or self._client.is_closed
            or self._client_loop is not loop
        ):
            if self._client is not None and not self._client.is_closed:
                try:
                    await self._client.aclose()
                except Exception:
                    pass
            self._client = httpx.AsyncClient(
                base_url=self._api_url,
                timeout=self._timeout,
                limits=self._limits,
                transport=httpx.AsyncHTTPTransport(retries=2),
                headers=datalab_headers(),
            )
            self._client_loop = loop
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
        try:
            sample = parse_create_sample_response(resp.json(), expected_id)
        except Exception as exc:
            # C16 补充：创建请求已成功（200）但响应校验失败（如 item_id
            # mismatch）时，sample 在 Datalab 端已真实创建，本地却无从记录
            # —— 不补偿删除会留下真孤儿。用响应中的实际 id 删除（回退
            # expected_id），再抛原始异常，避免吞掉真实失败原因。
            logger.error(
                "Datalab sample created but response invalid (item_id=%s): %s",
                expected_id,
                exc,
            )
            try:
                resp_body = resp.json()
                entry_raw = (
                    resp_body.get("sample_list_entry")
                    if isinstance(resp_body.get("sample_list_entry"), dict)
                    else resp_body
                )
                actual_id = str(entry_raw.get("item_id")) if isinstance(entry_raw, dict) else expected_id
                await self._delete_sample(actual_id or expected_id)
                logger.info("Saga rollback: deleted mismatched sample %s", actual_id or expected_id)
            except Exception as cleanup_exc:
                logger.error(
                    "Failed to clean up mismatched sample %s: %s",
                    expected_id,
                    cleanup_exc,
                )
            raise
        logger.info("Datalab created sample item_id=%s", sample.item_id)
        return sample

    async def _get_item(self, item_id: str) -> dict[str, Any] | None:
        client = await self._ensure_client()
        resp = await client.get(f"/get-item-data/{item_id}")
        if resp.status_code == 404:
            return None  # item deleted from Datalab, skip gracefully
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
        failed: list[str] = []
        for item_id in reversed(item_ids):
            try:
                await self._delete_sample(item_id)
                logger.info("Saga rollback: deleted sample %s", item_id)
            except Exception as exc:
                logger.error("Saga rollback failed for %s: %s", item_id, exc)
                failed.append(item_id)
        if failed:
            try:
                from .outbox_store import enqueue

                with commit_session(self._session_factory) as session:
                    for orphan_id in failed:
                        enqueue(
                            session,
                            operation="datalab_orphan_cleanup",
                            idempotency_key=f"orphan:{orphan_id}",
                            payload={"item_id": orphan_id, "kind": "campaign"},
                        )
                logger.info("Recorded %d orphan sample(s) for cleanup", len(failed))
            except Exception as exc:
                logger.error(
                    "Failed to record orphan samples for cleanup: %s; "
                    "orphan item_ids=%s",
                    exc,
                    failed,
                )

    async def create_from_plan(
        self,
        plan: DOEPlan,
        *,
        name: str | None = None,
        strategy: str = "BayBE-LHS",
        req: Requirement | None = None,
        project_id: str | None = None,
        owner_id: str | None = None,
    ) -> Campaign:
        campaign = self._create_campaign_meta(
            plan, name=name, strategy=strategy, req=req, project_id=project_id, owner_id=owner_id
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
                # 仅在实际创建成功后才记录，避免回滚时尝试删除从未创建的 item
                # 从而产生无意义的清理错误或掩盖真实失败原因（C16）。
                await self._create_sample(sample_data)
                created_item_ids.append(item_id)
                refs.append({"id": idx, "item_id": item_id})

            self._save_sample_refs(campaign.id, refs)
            # expire_on_commit=False keeps the detached instance on its loaded
            # value, so mirror the write onto the object we hand back — callers
            # use the return value directly instead of re-fetching.
            campaign.sample_refs = refs
            # P1: project-organise the DOE rows into a DataLab collection.
            # Best-effort — a collection failure must not fail the campaign.
            _coll = await self.sync_campaign_collection(campaign.id)
            if _coll:
                campaign.datalab_collection_id = _coll
            return campaign

        except Exception as exc:
            logger.error(
                "create_from_plan failed after %d/%d samples: %s",
                len(created_item_ids),
                len(plan.runs),
                exc,
            )
            await self._rollback_created_samples(created_item_ids)
            # Compensating transaction: the campaign never came into existence,
            # so drop the local row too. Marking it FAILED instead would leave a
            # sample-less shell that nothing reads — the failure is already on
            # the logger above, and unreachable orphans go to the outbox.
            with self._write_lock:
                with commit_session(self._session_factory) as session:
                    failed_campaign = session.get(Campaign, campaign.id)
                    if failed_campaign is not None:
                        session.delete(failed_campaign)
            raise DatalabUnavailableError(self._api_url, str(exc)) from exc

    async def get_campaign(self, campaign_id: int) -> Campaign | None:
        return self._get_campaign_sync(campaign_id)

    async def list_rows(self, campaign_id: int) -> list[WorkbenchRow]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return []
        out: list[WorkbenchRow] = []
        stale: list[str] = []
        for ref in campaign.sample_refs or []:
            row_id = int(ref["id"])
            item_id = str(ref["item_id"])
            try:
                item_data = await self._get_item(item_id)
            except httpx.HTTPError as exc:
                # Transient Datalab/network failure — keep the ref, skip this row.
                # Business errors (DatalabStoreError) still propagate.
                logger.warning("list_rows skip %s: %s", item_id, exc)
                continue
            if item_data is None:
                stale.append(item_id)
                continue
            out.append(_parse_row_from_item(campaign_id, row_id, item_id, item_data))
        if stale:
            # Auto-prune definitive 404s so the same campaign stops warning on
            # every subsequent read.
            await self._prune_stale_refs(campaign_id, stale)
        return out

    async def sync_campaign_collection(self, campaign_id: int) -> str | None:
        """P1: project-organise a campaign's DOE rows into a DataLab collection.

        Idempotent: re-uses an existing ``formumid_campaign_{id}`` collection and
        only records the mapping back into SQLite. Any failure is logged and
        swallowed (None) so campaign flows are never blocked by collection
        bookkeeping. Rows added to the campaign after the initial sync are not
        retro-attached (the add-items endpoint requires refcodes; revisit if DOE
        campaigns ever grow rows after creation).
        """
        try:
            campaign = self._get_campaign_sync(campaign_id)
            if campaign is None:
                return None
            coll_id = f"formumind_campaign_{campaign_id}"
            client = await self._ensure_client()

            if getattr(campaign, "datalab_collection_id", None):
                resp = await client.get(
                    f"/collections/{campaign.datalab_collection_id}"
                )
                if resp.status_code == 200:
                    return campaign.datalab_collection_id
                if resp.status_code not in (401, 403, 404):
                    resp.raise_for_status()
                logger.warning(
                    "campaign %s collection %s gone (%s) — recreating",
                    campaign_id,
                    campaign.datalab_collection_id,
                    resp.status_code,
                )

            refs = campaign.sample_refs or []
            payload = {
                "data": {
                    "collection_id": coll_id,
                    "title": f"FM-C{campaign_id} {campaign.name or ''}".strip(),
                    "starting_members": [
                        {"item_id": str(r.get("item_id"))} for r in refs if r.get("item_id")
                    ],
                }
            }
            resp = await client.put("/collections", json=payload)
            if resp.status_code == 409:
                logger.info(
                    "campaign %s collection %s already exists (created elsewhere)",
                    campaign_id,
                    coll_id,
                )
            elif resp.status_code not in (200, 201):
                resp.raise_for_status()

            with commit_session(self._session_factory) as session:
                row = session.get(Campaign, campaign_id)
                if row is not None:
                    row.datalab_collection_id = coll_id
            logger.info(
                "campaign %s → DataLab collection %s (%d DOE rows)",
                campaign_id,
                coll_id,
                len(refs),
            )
            return coll_id
        except Exception as exc:
            logger.warning(
                "sync_campaign_collection(campaign %s) failed: %s", campaign_id, exc
            )
            return None

    async def _prune_stale_refs(self, campaign_id: int, stale_ids: list[str]) -> None:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return
        stale_set = set(stale_ids)
        kept = [r for r in (campaign.sample_refs or []) if str(r.get("item_id")) not in stale_set]
        if len(kept) != len(campaign.sample_refs or []):
            self._save_sample_refs(campaign_id, kept)
            logger.warning(
                "list_rows auto-pruned %d stale ref(s) from campaign %s: %s",
                len(stale_set),
                campaign_id,
                sorted(stale_set),
            )

    async def reconcile_sample_refs(self, campaign_id: int) -> dict:
        """Detect and prune stale ``sample_refs``; only a definitive 404 prunes."""
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return {"removed": [], "kept": [], "removed_count": 0, "errors": []}
        refs = campaign.sample_refs or []
        kept: list[dict] = []
        removed: list[str] = []
        errors: list[str] = []
        for ref in refs:
            item_id = str(ref.get("item_id", ""))
            try:
                item_data = await self._get_item(item_id)
            except httpx.HTTPError as exc:
                logger.warning("reconcile skip %s: %s", item_id, exc)
                errors.append(item_id)
                kept.append(ref)  # keep on transient failure
                continue
            if item_data is None:
                removed.append(item_id)
            else:
                kept.append(ref)
        if removed:
            self._save_sample_refs(campaign_id, kept)
            logger.warning(
                "reconcile pruned %d stale ref(s) from campaign %s: %s",
                len(removed),
                campaign_id,
                removed,
            )
        return {
            "removed": removed,
            "kept": kept,
            "removed_count": len(removed),
            "errors": errors,
        }

    async def probe_sample_refs(self, campaign_id: int) -> dict:
        """Read-only stale-ref probe (no pruning) for the quality report."""
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return {"stale": [], "valid": [], "errors": []}
        stale: list[str] = []
        valid: list[str] = []
        errors: list[str] = []
        for ref in campaign.sample_refs or []:
            item_id = str(ref.get("item_id", ""))
            try:
                item_data = await self._get_item(item_id)
            except httpx.HTTPError as exc:
                logger.warning("probe_sample_refs skip %s: %s", item_id, exc)
                errors.append(item_id)
                continue
            if item_data is None:
                stale.append(item_id)
            else:
                valid.append(item_id)
        return {"stale": stale, "valid": valid, "errors": errors}

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
        failed = 0
        total_rows = len(rows)

        for payload in rows:
            row_id = int(payload["id"])
            item_id = ref_by_id.get(row_id)
            if not item_id:
                continue
            try:
                item_data = await self._get_item(item_id)
            except Exception as exc:
                logger.warning("batch_sync skip %s: %s", item_id, exc)
                failed += 1
                continue
            if item_data is None:
                logger.warning("batch_sync skip %s: item deleted from Datalab", item_id)
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
            note = payload["note"] if "note" in payload else params_block.get("note")
            tags = (
                list(payload["tags"] or [])
                if "tags" in payload
                else list(params_block.get("tags") or [])
            )

            blocks = _blocks_for_row(
                planned_params=planned,
                actual_params=actual,
                measurements=measurements,
                status=status,
                note=note,
                tags=tags,
            )
            # 合并而非整体替换：保留用户在 Datalab 上添加的其他 block，
            # 只更新 FormuMind 自有的两个键。
            existing_blocks = dict(item_data.get("blocks_obj") or {})
            existing_blocks.update(blocks)
            item_data["blocks_obj"] = existing_blocks
            merged_order = list(item_data.get("display_order") or [])
            for key in (_PARAMS_BLOCK, _MEASUREMENTS_BLOCK):
                if key not in merged_order:
                    merged_order.append(key)
            item_data["display_order"] = merged_order
            try:
                await self._save_item(item_id, item_data)
            except Exception as exc:
                logger.warning("batch_sync save failed for %s: %s", item_id, exc)
                failed += 1
                continue
            updated += 1

        if total_rows > 0 and failed == total_rows:
            # 整批全部失败（疑似 Datalab 不可达），不再静默返回 0 让上游误判成功
            raise DatalabUnavailableError(
                self._api_url,
                f"batch_sync 全部 {total_rows} 行失败（疑似 Datalab 不可达）",
            )
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
        owner_id: str | None = None,
    ) -> Campaign:
        campaign = self._create_campaign_meta(
            plan, name=name, strategy=strategy, req=req, project_id=project_id, owner_id=owner_id
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
                note=str(ref["note"]) if ref.get("note") else None,
                tags=list(ref.get("tags") or []),
                parent_sample_id=str(ref["parent_sample_id"]) if ref.get("parent_sample_id") else None,
                parent_campaign_id=int(ref["parent_campaign_id"]) if ref.get("parent_campaign_id") else None,
            )
            for ref in (campaign.sample_refs or [])
        ]

    async def list_rows(self, campaign_id: int) -> list[WorkbenchRow]:
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return []
        return self._refs_to_rows(campaign)

    async def set_row_tags(
        self, campaign_id: int, row_id: int, tags: list[str]
    ) -> WorkbenchRow | None:
        """只更新单行的 tags，不回写其他字段（避免读改写竞态覆盖并发编辑）。

        旧 update_row_tags 走 batch_sync 把整行（actual_params/measurements/note）
        回写，会覆盖并发期间别人改的字段（A9）。这里在读到的 item_data 上原地
        只改 tags 后保存，最大程度减小写窗口。
        """
        campaign = self._get_campaign_sync(campaign_id)
        if campaign is None:
            return None
        ref_by_id = {int(r["id"]): str(r["item_id"]) for r in (campaign.sample_refs or [])}
        item_id = ref_by_id.get(row_id)
        if not item_id:
            return None
        try:
            item_data = await self._get_item(item_id)
        except Exception as exc:
            logger.warning("set_row_tags skip %s: %s", item_id, exc)
            return None
        if item_data is None:
            return None
        blocks = dict(item_data.get("blocks_obj") or {})
        params_block = dict((blocks.get(_PARAMS_BLOCK) or {}).get("data") or {})
        params_block["tags"] = list(tags or [])
        blocks[_PARAMS_BLOCK] = datalab_block(_PARAMS_BLOCK, params_block)
        merged = dict(item_data.get("blocks_obj") or {})
        merged.update(blocks)
        item_data["blocks_obj"] = merged
        order = list(item_data.get("display_order") or [])
        if _PARAMS_BLOCK not in order:
            order.append(_PARAMS_BLOCK)
        item_data["display_order"] = order
        try:
            await self._save_item(item_id, item_data)
        except Exception as exc:
            logger.warning("set_row_tags save failed for %s: %s", item_id, exc)
            return None
        return next((r for r in self._refs_to_rows(campaign) if r.id == row_id), None)

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
            # Phase 2: persist note + tags
            if "note" in payload:
                ref["note"] = payload["note"]
            if "tags" in payload:
                ref["tags"] = list(payload["tags"] or [])
            updated += 1

        self._save_sample_refs(campaign_id, refs)
        campaign.sample_refs = refs
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
