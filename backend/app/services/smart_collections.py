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
sweeps a project's due collections. A thin celery task wrapper
(``formumind.collection_refresh`` in ``worker/tasks.py``) exists for real
periodic runs; beat stays opt-in per ``celery_app.py``.

P0-8 guard: items whose ``screening_source == "human"`` are never touched by
collection refreshes, and any pre-existing non-unset screening decision on an
item is preserved — refreshes only *add* new candidates.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
SCHEMA_VERSION = 1
_SNAPSHOT_CAP = 10
_DEFAULT_INTERVAL_HOURS = 24.0


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


def _load_store(project_id: str) -> dict[str, Any]:
    path = collections_path(project_id)
    if not path.is_file():
        return _empty_store(project_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _empty_store(project_id)
        raw.setdefault("project_id", project_id)
        raw.setdefault("collections", [])
        return raw
    except Exception as exc:  # noqa: BLE001
        logger.warning("smart collections load failed: %s", exc)
        return _empty_store(project_id)


def _save_store(project_id: str, store: dict[str, Any]) -> dict[str, Any]:
    path = collections_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    store["project_id"] = project_id
    store["schema_version"] = SCHEMA_VERSION
    with _LOCK:
        path.write_text(
            json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return store


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
    store = _load_store(project_id)
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
    _save_store(project_id, store)
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
    store = _load_store(project_id)
    for col in store.get("collections") or []:
        if col.get("collection_id") != collection_id:
            continue
        if name is not None:
            col["name"] = name.strip()
        if query is not None:
            col["query"] = query.strip()
        if filters is not None:
            col["filters"] = _norm_filters(filters)
        if screening_preset is not None:
            col["screening_preset"] = screening_preset.strip() or None
        if schedule is not None:
            # Merge over the existing schedule so partial updates (e.g. only
            # toggling `enabled`) keep interval_hours / last_run.
            merged = dict(col.get("schedule") or {})
            merged.update({k: v for k, v in schedule.items()})
            col["schedule"] = _norm_schedule(merged)
        col["updated_at"] = _now()
        _save_store(project_id, store)
        return dict(col)
    return None


def delete_collection(project_id: str, collection_id: str) -> bool:
    store = _load_store(project_id)
    before = len(store.get("collections") or [])
    store["collections"] = [
        c
        for c in (store.get("collections") or [])
        if c.get("collection_id") != collection_id
    ]
    if len(store["collections"]) == before:
        return False
    _save_store(project_id, store)
    return True


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
    query: str, filters: dict[str, Any], settings: Any = None
) -> list[Any]:
    from .federated_search import FederatedSearchEngine

    engine = FederatedSearchEngine(settings)
    result = engine.search(
        query,
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
        domain_allowlist=filters.get("domain_allowlist"),
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
    """Run the saved query now; merge hits; record a snapshot with id diff."""
    store = _load_store(project_id)
    col = next(
        (
            c
            for c in (store.get("collections") or [])
            if c.get("collection_id") == collection_id
        ),
        None,
    )
    if col is None:
        raise KeyError(f"collection not found: {collection_id}")
    filters = dict(col.get("filters") or {})
    prev_ids = set()
    if col.get("snapshots"):
        prev_ids = set((col["snapshots"][-1] or {}).get("item_ids") or [])

    evidence = _run_search(col["query"], filters, settings=settings)
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
    _save_store(project_id, store)
    return dict(snapshot)


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
