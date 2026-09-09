"""Optional thin Neo4j projection of wiki entities (W4d)."""
from __future__ import annotations

import logging

from ...config import get_settings
from ...db.wiki_store import get_wiki_store

logger = logging.getLogger(__name__)


def project_paths(paths: list[str]) -> dict:
    """Best-effort upsert Compound nodes for wiki entity_ids."""
    settings = get_settings()
    if (
        not settings.wiki_enabled
        or not getattr(settings, "wiki_neo4j_project", False)
        or not settings.neo4j_enabled
    ):
        return {"ok": False, "reason": "disabled", "projected": 0}
    try:
        from ..neo4j_kg import upsert_compound
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc), "projected": 0}

    store = get_wiki_store()
    n = 0
    for path in paths:
        row = store.get_by_path(path)
        if row is None or not row.entity_id:
            continue
        cas = None
        if row.entity_id.startswith("chem:cas:"):
            cas = row.entity_id.split("chem:cas:", 1)[1]
        try:
            if upsert_compound(
                row.entity_id,
                row.title or row.norm_key or row.entity_id,
                cas_number=cas,
                notes=f"wiki:{row.path}",
            ):
                n += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("wiki neo4j project %s failed: %s", path, exc)
    return {"ok": True, "projected": n}
