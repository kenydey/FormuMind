"""Smart Collections API (W6-3 / P2-3).

Thin HTTP adapter over ``services.smart_collections``. Auth is enforced by the
global bearer-token middleware, same as the other routers.
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import smart_collections as sc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/collections", tags=["collections"])

# ── W6-3 自动刷新 ─────────────────────────────────────────────────────────
# GET list 顺带触发到期集合的后台刷新（daemon 线程，fail-open），兑现方案
# "定时刷新（默认每天一次）"——不依赖 celery beat 是否配置。
# B-4：_AUTO_REFRESH_IN_FLIGHT 只做同进程去重标记，由 _AUTO_REFRESH_MARK_LOCK
# 做短临界区保护；耗时搜索在 services.refresh_collection 内部已移出 store 锁，
# 快照持久化按 project 串行（services._project_txn）。GET list 只取短锁做
# 标记，刷新在途时不被长时间阻塞。
_AUTO_REFRESH_IN_FLIGHT: set[tuple[str, str]] = set()
_AUTO_REFRESH_MARK_LOCK = threading.Lock()


def _auto_refresh_worker(project_id: str, collection_id: str) -> None:
    key = (project_id, collection_id)
    try:
        sc.refresh_collection(project_id, collection_id, actor="auto")
    except Exception as exc:  # noqa: BLE001 — 后台刷新永不炸主请求
        logger.warning("smart collection auto-refresh failed %s: %s", key, exc)
    finally:
        with _AUTO_REFRESH_MARK_LOCK:
            _AUTO_REFRESH_IN_FLIGHT.discard(key)


def _kick_auto_refresh(project_id: str, collections: list[dict] | None) -> None:
    """为到期且未在刷新的集合各起一个 daemon 线程做后台刷新。"""
    try:
        enabled = bool(get_settings().smart_collections_auto_refresh)
    except Exception:  # noqa: BLE001
        return
    if not enabled:
        return
    for col in collections or []:
        cid = (col or {}).get("collection_id")
        if not cid:
            continue
        try:
            due = sc.is_due(col)
        except Exception:  # noqa: BLE001
            continue
        if not due:
            continue
        key = (project_id, cid)
        with _AUTO_REFRESH_MARK_LOCK:
            if key in _AUTO_REFRESH_IN_FLIGHT:
                continue
            _AUTO_REFRESH_IN_FLIGHT.add(key)
        thread = threading.Thread(
            target=_auto_refresh_worker,
            args=(project_id, cid),
            daemon=True,
            name=f"collection-autorefresh-{str(cid)[:8]}",
        )
        thread.start()


class FiltersIn(BaseModel):
    date_from: str | int | None = None
    date_to: str | int | None = None
    domain_allowlist: list[str] | None = None


class ScheduleIn(BaseModel):
    enabled: bool = True
    interval_hours: float = 24.0


class CollectionCreate(BaseModel):
    project_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    filters: FiltersIn | None = None
    screening_preset: str | None = None
    schedule: ScheduleIn | None = None


class CollectionUpdate(BaseModel):
    name: str | None = None
    query: str | None = None
    filters: FiltersIn | None = None
    screening_preset: str | None = None
    schedule: ScheduleIn | None = None


def _get_or_404(project_id: str, collection_id: str) -> dict:
    col = sc.get_collection(project_id, collection_id)
    if col is None:
        raise HTTPException(status_code=404, detail="collection not found")
    return col


@router.get("")
def list_collections(project_id: str = Query(..., min_length=1)):
    cols = sc.list_collections(project_id)
    # 到期集合顺带后台刷新（fail-open，不阻塞列表返回）。
    _kick_auto_refresh(project_id, cols)
    return {"project_id": project_id, "collections": cols}


@router.post("", status_code=201)
def create_collection(body: CollectionCreate):
    try:
        col = sc.create_collection(
            body.project_id,
            name=body.name,
            query=body.query,
            filters=body.filters.model_dump() if body.filters else None,
            screening_preset=body.screening_preset,
            schedule=body.schedule.model_dump() if body.schedule else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return col


@router.get("/{collection_id}")
def get_collection_detail(
    collection_id: str, project_id: str = Query(..., min_length=1)
):
    return _get_or_404(project_id, collection_id)


@router.patch("/{collection_id}")
def update_collection(
    collection_id: str,
    body: CollectionUpdate,
    project_id: str = Query(..., min_length=1),
):
    try:
        # B-13：PATCH 部分更新语义 —— exclude_unset 避免模型默认值覆盖未提供字段；
        # filters 在 service 层与现有值合并（显式 null 清键），而非全量替换。
        col = sc.update_collection(
            project_id,
            collection_id,
            name=body.name,
            query=body.query,
            filters=body.filters.model_dump(exclude_unset=True) if body.filters else None,
            screening_preset=body.screening_preset,
            schedule=body.schedule.model_dump(exclude_unset=True) if body.schedule else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if col is None:
        raise HTTPException(status_code=404, detail="collection not found")
    return col


@router.delete("/{collection_id}")
def delete_collection(
    collection_id: str, project_id: str = Query(..., min_length=1)
):
    ok = sc.delete_collection(project_id, collection_id)
    if not ok:
        raise HTTPException(status_code=404, detail="collection not found")
    return {"deleted": True, "collection_id": collection_id}


@router.post("/{collection_id}/refresh")
def refresh_collection(
    collection_id: str, project_id: str = Query(..., min_length=1)
):
    try:
        snap = sc.refresh_collection(
            project_id, collection_id, settings=get_settings(), actor="user"
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="collection not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return snap
