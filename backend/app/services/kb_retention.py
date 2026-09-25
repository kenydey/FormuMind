"""W4: flag-gated purge of soft-archived KB sources past a retention window.

Never runs automatically — callers must POST with an explicit ``days`` and
``confirm=true`` for physical delete. Default path is dry-run.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def purge_expired_archived(
    *,
    days: int,
    dry_run: bool = True,
    confirm: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Hard-delete archived sources older than ``days``.

    Returns a summary. Raises ``ValueError`` for invalid args.
    """
    d = int(days)
    if d < 1:
        raise ValueError("days must be >= 1")
    lim = max(1, min(int(limit or 200), 1000))

    from ..db.source_store import get_source_store
    from .kb_delete import delete_kb_source

    store = get_source_store()
    rows = store.list_archived_expired(days=d, limit=lim)
    candidates = [
        {
            "source_id": r.id,
            "title": r.title or r.filename,
            "archived_at": (r.archived_at or r.created_at).isoformat()
            if (r.archived_at or r.created_at)
            else None,
        }
        for r in rows
    ]
    if dry_run or not confirm:
        return {
            "ok": True,
            "dry_run": True,
            "days": d,
            "candidates": candidates,
            "candidate_count": len(candidates),
            "purged": [],
            "purged_count": 0,
            "errors": [],
        }

    purged: list[str] = []
    errors: list[dict[str, str]] = []
    for row in rows:
        try:
            delete_kb_source(row.id)
            purged.append(row.id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("retention purge failed for %s", row.id)
            errors.append({"source_id": row.id, "error": str(exc)})
    return {
        "ok": len(errors) == 0,
        "dry_run": False,
        "days": d,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "purged": purged,
        "purged_count": len(purged),
        "errors": errors,
    }
