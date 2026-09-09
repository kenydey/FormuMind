"""Cascade-delete a persisted KB SourceDocument (Knowledge Hub H2).

Removes chunks, KG mentions/links, wiki source_id refs, then the source row.
Never raises to callers for optional wiki/product cleanup failures.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def delete_kb_source(source_id: str) -> dict[str, Any]:
    """Hard-delete one source and dependent index rows.

    Returns a summary dict. Raises ``KeyError`` if the source does not exist.
    """
    sid = (source_id or "").strip()
    if not sid:
        raise KeyError("missing source_id")

    from ..db.chunk_store import get_chunk_store
    from ..db.source_store import get_source_store

    store = get_source_store()
    doc = store.get(sid)
    if doc is None:
        raise KeyError(sid)

    chunks_removed = 0
    mentions_removed = 0
    links_removed = 0
    wiki_pages_touched = 0

    try:
        chunks_removed = int(get_chunk_store().delete_for_source(sid) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.exception("kb delete: chunks failed for %s: %s", sid, exc)

    try:
        from ..db.entity_store import get_entity_store

        ents = get_entity_store()
        mentions_removed = int(ents.delete_mentions_for_source(sid) or 0)
        links_removed = int(ents.delete_links_for_source(sid) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb delete: kg cleanup skipped: %s", exc)

    try:
        wiki_pages_touched = _strip_wiki_source_ids(sid)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb delete: wiki cleanup skipped: %s", exc)

    try:
        _strip_product_source_ids(sid)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb delete: product cleanup skipped: %s", exc)

    store.delete(sid)

    return {
        "ok": True,
        "source_id": sid,
        "chunks_removed": chunks_removed,
        "mentions_removed": mentions_removed,
        "links_removed": links_removed,
        "wiki_pages_touched": wiki_pages_touched,
    }


def _strip_wiki_source_ids(source_id: str) -> int:
    from ..db.wiki_store import get_wiki_store

    wiki = get_wiki_store()
    touched = 0
    for row in wiki.list_pages(limit=500):
        ids = list(row.source_ids or [])
        if source_id not in ids:
            continue
        new_ids = [x for x in ids if x != source_id]
        md = wiki.read_markdown(row.path) or ""
        if md.startswith("---"):
            end = md.find("\n---", 3)
            if end > 0:
                header = md[3:end]
                rest = md[end + 4 :]
                lines = []
                replaced = False
                src_list = ", ".join(f'"{s}"' for s in new_ids)
                for ln in header.splitlines():
                    if ln.strip().startswith("source_ids:"):
                        lines.append(f"source_ids: [{src_list}]")
                        replaced = True
                    else:
                        lines.append(ln)
                if not replaced:
                    lines.append(f"source_ids: [{src_list}]")
                flags = list(row.flags or [])
                if not new_ids and "stale" not in flags:
                    flags.append("stale")
                    flag_list = ", ".join(f'"{f}"' for f in flags)
                    lines2 = []
                    flag_done = False
                    for ln in lines:
                        if ln.strip().startswith("flags:"):
                            lines2.append(f"flags: [{flag_list}]")
                            flag_done = True
                        else:
                            lines2.append(ln)
                    if not flag_done:
                        lines2.append(f"flags: [{flag_list}]")
                    lines = lines2
                new_md = "---\n" + "\n".join(lines) + "\n---" + rest
                wiki.upsert_page(
                    path=row.path,
                    kind=row.kind,
                    title=row.title or "",
                    norm_key=row.norm_key or "",
                    entity_id=row.entity_id,
                    markdown=new_md,
                    source_ids=new_ids,
                    flags=flags,
                    replace_source_ids=True,
                )
                touched += 1
                continue
        # Fallback: update DB row only
        wiki.upsert_page(
            path=row.path,
            kind=row.kind,
            title=row.title or "",
            norm_key=row.norm_key or "",
            entity_id=row.entity_id,
            markdown=md or f"# {row.title}\n",
            source_ids=new_ids,
            flags=list(row.flags or []) + (["stale"] if not new_ids else []),
            replace_source_ids=True,
        )
        touched += 1
    return touched


def _strip_product_source_ids(source_id: str) -> None:
    from ..db.product_store import get_product_store
    from ..db.models import KBProduct
    from ..db.session_utils import commit_session
    from ..db.database import default_session_factory

    store = get_product_store()
    for prod in store.search("", limit=500):
        ids = list(prod.source_ids or [])
        if source_id not in ids:
            continue
        new_ids = [x for x in ids if x != source_id]
        with commit_session(default_session_factory()) as session:
            row = session.get(KBProduct, prod.id)
            if row is not None:
                row.source_ids = new_ids
