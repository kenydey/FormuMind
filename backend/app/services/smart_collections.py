"""Smart Collections (W6-3 / P2-3): saved query + filter sets with scheduled refresh.

A collection is a named, project-scoped ``{query, filters, screening_preset,
schedule}`` bundle. Refreshing a collection runs the saved query through
``FederatedSearchEngine`` (reusing the W4-6 date/domain post-filters), merges
the hits into the project literature manifest with
``screening_source="collection"``, and records a snapshot carrying an
entry-level added/removed diff (W4-3 diff idea reduced to id sets — no need
for char-level diffing on id lists). Snapshots are capped at the newest 10.

Scheduling model: ``schedule = {enabled, interval_hours, last_run}`` (default
enabled, 24h). ``is_due()`` computes the next due time; ``refresh_due_collections()``
sweeps a project's due collections. Refresh is triggered opportunistically via
the collections list endpoint (background sweep); celery beat stays opt-in per
``celery_app.py``.

P0-8 guard: items whose ``screening_source == "human"`` are never touched by
collection refreshes, and any pre-existing non-unset screening decision on an
item is preserved — refreshes only *add* new candidates.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ._filelock import lock_exclusive, unlock
from ._fsutil import read_text_with_retry, replace_with_retry

logger = logging.getLogger(__name__)

# ── B-3 并发与原子写 ──────────────────────────────────────────────────
# 每个 project 一把锁，覆盖完整 load-modify-save 事务；写盘用同目录临时文件
# + flush/fsync + os.replace 原子替换。损坏文件隔离为 .corrupt-* 备份并打
# error 日志；_CORRUPT_SEEN 记住"本进程已见过损坏"，之后同 project 的写事务
# 直接失败，绝不把空 store 写回去（备份恢复后写保护自动解除）。
_PROJECT_LOCKS: dict[str, threading.Lock] = {}
_PROJECT_LOCKS_GUARD = threading.Lock()
_CORRUPT_SEEN: set[str] = set()
SCHEMA_VERSION = 1
_SNAPSHOT_CAP = 10
_DEFAULT_INTERVAL_HOURS = 24.0


class StoreCorruptError(RuntimeError):
    """collections.json 已损坏（已隔离为 .corrupt-* 备份）；拒绝用空 store 覆盖。"""


def _data_root() -> Path:
    return Path("./data").resolve()


def _safe_project(project_id: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", (project_id or "").strip())[:120] or "unknown"


def collections_path(project_id: str) -> Path:
    return _data_root() / "collections" / _safe_project(project_id) / "collections.json"


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _empty_store(project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "schema_version": SCHEMA_VERSION,
        "collections": [],
    }


def _project_lock(project_id: str) -> threading.Lock:
    """按 project 取锁（key 用落盘目录名，保证锁与文件一一对应）。"""
    key = _safe_project(project_id)
    with _PROJECT_LOCKS_GUARD:
        lock = _PROJECT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROJECT_LOCKS[key] = lock
        return lock


def _mark_corrupt_seen(project_id: str) -> None:
    with _PROJECT_LOCKS_GUARD:
        _CORRUPT_SEEN.add(_safe_project(project_id))


def _quarantine_corrupt(path: Path, exc: Exception) -> None:
    """把损坏的 store 文件改名隔离，绝不删除用户数据。"""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    backup = path.with_name(f"{path.name}.corrupt-{stamp}-{uuid.uuid4().hex[:6]}")
    try:
        path.rename(backup)
    except FileNotFoundError:
        # 已被其他线程隔离
        logger.error(
            "smart collections store %s corrupted (already quarantined): %s",
            path.name,
            exc,
        )
        return
    except OSError as exc2:  # noqa: BLE001
        logger.error(
            "smart collections store %s corrupted; quarantine failed: %s (%s)",
            path.name,
            exc,
            exc2,
        )
        return
    logger.error(
        "smart collections store %s corrupted; quarantined to %s: %s",
        path.name,
        backup.name,
        exc,
    )


def _load_store_strict(project_id: str) -> tuple[dict[str, Any], bool]:
    """返回 (store, 腐坏标记)。

    文件损坏时隔离备份并返回空 store + True；调用方（尤其是写事务）必须
    检查标记，绝不把空 store 写回去。
    """
    path = collections_path(project_id)
    if not path.is_file():
        return _empty_store(project_id), False
    try:
        raw = json.loads(read_text_with_retry(path))
    except OSError:
        # 读不出来 ≠ 损坏：Windows 上写方的 os.replace 瞬间会让健康文件短暂不可读。把它当损坏隔离，
        # 就是把一份完好的 store 改名挪走并回一个空的——唯一错误的答案。上抛，让调用方失败而不是丢数据。
        raise
    except Exception as exc:  # noqa: BLE001 — 解析失败即视为损坏
        _quarantine_corrupt(path, exc)
        _mark_corrupt_seen(project_id)
        return _empty_store(project_id), True
    if not isinstance(raw, dict) or not isinstance(raw.get("collections", []), list):
        _quarantine_corrupt(path, ValueError(f"unexpected store shape: {type(raw).__name__}"))
        _mark_corrupt_seen(project_id)
        return _empty_store(project_id), True
    raw.setdefault("project_id", project_id)
    raw.setdefault("collections", [])
    return raw, False


def _load_store(project_id: str) -> dict[str, Any]:
    store, _corrupt = _load_store_strict(project_id)
    return store


def _fsync_dir(d: Path) -> None:
    # Windows has no os.O_DIRECTORY (and cannot fsync a directory): the attribute access raised AttributeError, which
    # the ``except OSError`` below does not catch — every write of a smart collection failed there.
    flag = getattr(os, "O_DIRECTORY", None)
    if flag is None:
        return
    try:
        fd = os.open(d, flag)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _save_store(project_id: str, store: dict[str, Any]) -> dict[str, Any]:
    """原子写盘：同目录临时文件 + flush/fsync + os.replace。

    读方永远看到旧全本或新全本，不会读到半截 JSON。调用方须已持有
    project 事务锁（见 _project_txn）。
    """
    path = collections_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    store["project_id"] = project_id
    store["schema_version"] = SCHEMA_VERSION
    payload = json.dumps(store, ensure_ascii=False, indent=2).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        replace_with_retry(tmp_name, path)
        _fsync_dir(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return store


@contextlib.contextmanager
def _file_lock(path: Path):
    """跨进程排他锁（B-3）：多 worker 部署时防止进程间并发写丢更新。

    单进程部署时 threading.Lock 已足够，文件锁是第二道防线（POSIX flock / Windows msvcrt，见 _filelock）。
    锁文件与数据文件同目录，保证同文件系统。
    """
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as f:
        lock_exclusive(f)
        try:
            yield
        finally:
            unlock(f)


@contextlib.contextmanager
def _project_txn(project_id: str, *, readonly: bool = False):
    """按 project 加锁的 load-modify-save 事务。

    损坏的 store 直接抛 StoreCorruptError（文件已隔离备份，_CORRUPT_SEEN
    已标记），调用方失败，绝不把空 store 写回去。
    """
    key = _safe_project(project_id)
    lock = _project_lock(project_id)
    with lock, _file_lock(collections_path(project_id)):
        if key in _CORRUPT_SEEN and not collections_path(project_id).is_file():
            # 本进程已见过损坏，文件尚未恢复：拒绝写，绝不把空 store 写回去。
            # 运维恢复 .corrupt-* 备份后写保护自动解除（或重启进程）。
            raise StoreCorruptError(
                f"collections store for project {project_id!r} is corrupted; "
                "quarantined as .corrupt-* backup, refusing to overwrite"
            )
        store, corrupt = _load_store_strict(project_id)
        if corrupt:
            raise StoreCorruptError(
                f"collections store for project {project_id!r} is corrupted; "
                "quarantined as .corrupt-* backup, refusing to overwrite"
            )
        with _PROJECT_LOCKS_GUARD:
            # 文件存在且解析正常 → 备份已被运维恢复，解除写保护
            _CORRUPT_SEEN.discard(key)
        yield store
        if not readonly:
            _save_store(project_id, store)


def _find_col(store: dict[str, Any], collection_id: str) -> dict[str, Any] | None:
    for col in store.get("collections") or []:
        if col.get("collection_id") == collection_id:
            return col
    return None


def _norm_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    f = dict(filters or {})
    out: dict[str, Any] = {}
    if f.get("date_from") not in (None, ""):
        out["date_from"] = f["date_from"]
    if f.get("date_to") not in (None, ""):
        out["date_to"] = f["date_to"]
    allow = f.get("domain_allowlist") or []
    if isinstance(allow, str):
        allow = [s.strip() for s in allow.split(",") if s.strip()]
    allow = [str(s).strip() for s in allow if str(s).strip()]
    if allow:
        out["domain_allowlist"] = allow
    return out


def _norm_schedule(schedule: dict[str, Any] | None) -> dict[str, Any]:
    s = dict(schedule or {})
    try:
        interval = float(s.get("interval_hours", _DEFAULT_INTERVAL_HOURS))
    except (TypeError, ValueError):
        interval = _DEFAULT_INTERVAL_HOURS
    interval = max(1.0, interval)
    return {
        "enabled": bool(s.get("enabled", True)),
        "interval_hours": interval,
        "last_run": s.get("last_run"),
    }


def _summarize(col: dict[str, Any]) -> dict[str, Any]:
    snaps = col.get("snapshots") or []
    last = snaps[-1] if snaps else None
    return {
        "collection_id": col.get("collection_id"),
        "name": col.get("name"),
        "query": col.get("query"),
        "filters": col.get("filters") or {},
        "screening_preset": col.get("screening_preset"),
        "schedule": col.get("schedule") or {},
        "created_at": col.get("created_at"),
        "updated_at": col.get("updated_at"),
        "snapshot_count": len(snaps),
        "last_snapshot": (
            {
                "snapshot_id": last.get("snapshot_id"),
                "at": last.get("at"),
                "total": last.get("total"),
                "added": len(last.get("added") or []),
                "removed": len(last.get("removed") or []),
            }
            if last
            else None
        ),
    }


def create_collection(
    project_id: str,
    *,
    name: str,
    query: str,
    filters: dict[str, Any] | None = None,
    screening_preset: str | None = None,
    schedule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = (name or "").strip()
    query = (query or "").strip()
    if not name:
        raise ValueError("name is required")
    if not query:
        raise ValueError("query is required")
    with _project_txn(project_id) as store:
        col = {
            "collection_id": _new_id("col"),
            "name": name,
            "query": query,
            "filters": _norm_filters(filters),
            "screening_preset": (screening_preset or "").strip() or None,
            "schedule": _norm_schedule(schedule),
            "created_at": _now(),
            "updated_at": _now(),
            "snapshots": [],
        }
        store["collections"].append(col)
    return dict(col)


def list_collections(project_id: str) -> list[dict[str, Any]]:
    store = _load_store(project_id)
    return [_summarize(c) for c in (store.get("collections") or [])]


def get_collection(project_id: str, collection_id: str) -> dict[str, Any] | None:
    store = _load_store(project_id)
    for col in store.get("collections") or []:
        if col.get("collection_id") == collection_id:
            return dict(col)
    return None


def _merge_filters(
    existing: dict[str, Any] | None, patch: dict[str, Any] | None
) -> dict[str, Any]:
    """filters PATCH 合并语义（B-13）：

    - 只传的键覆盖；
    - 显式传 null/"" 清掉该键；
    - 没传的键保留原值。
    """
    merged = dict(existing or {})
    for key, value in (patch or {}).items():
        if value in (None, ""):
            merged.pop(key, None)
        else:
            merged[key] = value
    return _norm_filters(merged)


def update_collection(
    project_id: str,
    collection_id: str,
    *,
    name: str | None = None,
    query: str | None = None,
    filters: dict[str, Any] | None = None,
    screening_preset: str | None = None,
    schedule: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if name is not None and not name.strip():
        raise ValueError("name must not be empty")
    if query is not None and not query.strip():
        raise ValueError("query must not be empty")
    with _project_txn(project_id) as store:
        col = _find_col(store, collection_id)
        if col is None:
            return None
        if name is not None:
            col["name"] = name.strip()
        if query is not None:
            col["query"] = query.strip()
        if filters is not None:
            # B-13：合并而非全量替换，未提供的键保留
            col["filters"] = _merge_filters(col.get("filters"), filters)
        if screening_preset is not None:
            col["screening_preset"] = screening_preset.strip() or None
        if schedule is not None:
            # Merge over the existing schedule so partial updates (e.g. only
            # toggling `enabled`) keep interval_hours / last_run.
            merged = dict(col.get("schedule") or {})
            merged.update({k: v for k, v in schedule.items()})
            col["schedule"] = _norm_schedule(merged)
        col["updated_at"] = _now()
        return dict(col)
    return None  # pragma: no cover — 事务内已 return，仅防卫


def delete_collection(project_id: str, collection_id: str) -> bool:
    with _project_txn(project_id) as store:
        cols = store.get("collections") or []
        kept = [c for c in cols if c.get("collection_id") != collection_id]
        if len(kept) == len(cols):
            return False
        store["collections"] = kept
        return True
    return False  # pragma: no cover — 事务内已 return，仅防卫


def is_due(collection: dict[str, Any], now: float | None = None) -> bool:
    """True when the collection's schedule says it should refresh now."""
    sched = collection.get("schedule") or {}
    if not sched.get("enabled", True):
        return False
    now = now if now is not None else _now()
    last = sched.get("last_run")
    if last is None:
        return True  # never refreshed → due
    try:
        interval = float(sched.get("interval_hours", _DEFAULT_INTERVAL_HOURS))
    except (TypeError, ValueError):
        interval = _DEFAULT_INTERVAL_HOURS
    return (now - float(last)) >= interval * 3600.0


def _run_search(
    query: str, filters: dict[str, Any], settings: Any = None,
    # v15: per-project NotebookLM notebook（None = 用全局配置）
    notebooklm_notebook_id: str | None = None,
) -> list[Any]:
    from .federated_search import FederatedSearchEngine

    engine = FederatedSearchEngine(settings)
    result = engine.search(
        query,
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
        domain_allowlist=filters.get("domain_allowlist"),
        notebooklm_notebook_id=notebooklm_notebook_id,
    )
    return list(result.evidence or [])


def _merge_into_manifest(
    project_id: str, evidence: list[Any], settings: Any = None
) -> tuple[int, int]:
    """Merge evidence hits into the literature manifest.

    Returns (added_count, skipped_existing_count). New items get
    ``screening_source="collection"``; existing items (including human
    dispositions per P0-8) are never overwritten.
    """
    from . import literature_manifest as lm

    man = lm.load_manifest(project_id)
    by_id: dict[str, dict[str, Any]] = {
        str(i.get("id")): i for i in (man.get("items") or [])
    }
    added = 0
    skipped = 0
    for ev in evidence:
        item = lm._item_from_evidence(ev)  # same row shape as manifest capture
        iid = str(item.get("id") or "")
        if not iid:
            continue
        if iid in by_id:
            skipped += 1
            continue
        item["screening"] = "unset"
        item["screening_source"] = "collection"
        by_id[iid] = item
        added += 1
    man["items"] = list(by_id.values())
    man["events"] = (man.get("events") or [])[-180:] + [
        {
            "type": "collection_refreshed",
            "at": _now(),
            "added": added,
            "skipped_existing": skipped,
        }
    ]
    lm.save_manifest(man)
    return added, skipped


def refresh_collection(
    project_id: str,
    collection_id: str,
    *,
    settings: Any = None,
    actor: str = "user",
) -> dict[str, Any]:
    """Run the saved query now; merge hits; record a snapshot with id diff.

    B-4：耗时外部搜索在 project 事务锁之外执行，后台刷新在途时不阻塞
    其他请求；快照持久化（含 manifest 合并）在事务锁内按 project 串行。
    """
    # 阶段 1：只读集合定义（query/filters），快速释放锁。
    with _project_txn(project_id, readonly=True) as store:
        col = _find_col(store, collection_id)
        if col is None:
            raise KeyError(f"collection not found: {collection_id}")
        query = col.get("query") or ""
        filters = dict(col.get("filters") or {})

    # v15: per-project NotebookLM notebook（项目 workspace 配置；失败时降级全局）
    nb_id: str | None = None
    try:
        from ..db.project_store import get_project_store

        detail = get_project_store().get(project_id)
        if detail is not None and detail.workspace is not None:
            nb_id = detail.workspace.notebooklm_notebook_id
    except Exception:
        nb_id = None

    # 阶段 2：耗时 IO（外部文献搜索），不持有任何 store 锁。
    evidence = _run_search(query, filters, settings=settings, notebooklm_notebook_id=nb_id)

    # 阶段 3：持久化（manifest 合并 + 快照），按 project 串行。
    with _project_txn(project_id) as store:
        col = _find_col(store, collection_id)
        if col is None:
            raise KeyError(f"collection not found: {collection_id}")
        prev_ids = set()
        if col.get("snapshots"):
            prev_ids = set((col["snapshots"][-1] or {}).get("item_ids") or [])

        added_to_manifest, skipped_existing = _merge_into_manifest(
            project_id, evidence, settings=settings
        )

        # Snapshot reflects the *search result* id set (stable even if manifest
        # merges are partial), so added/removed describe the collection itself.
        from . import literature_manifest as lm

        cur_ids = sorted({str(lm._item_from_evidence(ev).get("id") or "") for ev in evidence} - {""})
        added = sorted(set(cur_ids) - prev_ids)
        removed = sorted(prev_ids - set(cur_ids))
        snapshot = {
            "snapshot_id": _new_id("snap"),
            "at": _now(),
            "actor": actor,
            "query": col["query"],
            "filters": filters,
            "item_ids": cur_ids,
            "added": added,
            "removed": removed,
            "total": len(cur_ids),
            "manifest_added": added_to_manifest,
            "manifest_skipped_existing": skipped_existing,
        }
        col["snapshots"] = (col.get("snapshots") or []) + [snapshot]
        # Cap: keep the newest 10 snapshots.
        if len(col["snapshots"]) > _SNAPSHOT_CAP:
            col["snapshots"] = col["snapshots"][-_SNAPSHOT_CAP:]
        sched = col.get("schedule") or {}
        sched["last_run"] = snapshot["at"]
        col["schedule"] = sched
        col["updated_at"] = _now()
        return dict(snapshot)
    raise AssertionError("unreachable")  # pragma: no cover


def refresh_due_collections(
    project_id: str, *, settings: Any = None, actor: str = "scheduler"
) -> list[dict[str, Any]]:
    """Refresh every due collection; fail-open per collection."""
    store = _load_store(project_id)
    results: list[dict[str, Any]] = []
    for col in store.get("collections") or []:
        cid = col.get("collection_id")
        if not cid or not is_due(col):
            continue
        try:
            snap = refresh_collection(
                project_id, cid, settings=settings, actor=actor
            )
            results.append(
                {"collection_id": cid, "ok": True, "snapshot_id": snap["snapshot_id"]}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduled refresh failed for %s: %s", cid, exc)
            results.append({"collection_id": cid, "ok": False, "error": str(exc)})
    return results
