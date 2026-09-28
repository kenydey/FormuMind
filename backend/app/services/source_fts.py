"""SQLite FTS5 index over KB full-text chunks (W2-1 / P1-6).

Page-level FTS already exists for Wiki pages (``services/wiki/fts.py``); this
module adds the *chunk-level* counterpart for PDF full-text chunks persisted by
``kb_index.index_source``. Each row carries ``page_no`` / ``section_title`` /
character offsets so later stages (P1-7 read API, RAG citations) can return
page-anchored passages.

Design notes (mirroring ``wiki/fts.py``):

* CJK handling: ``unicode61`` does not substring-match contiguous CJK, so the
  *indexed* search columns (``search_section`` / ``search_text``) carry the
  CJK-expanded form while the display columns (``section_title`` / ``text``)
  keep the raw text — search results never show space-mangled CJK.
* ``section_title`` is indexed as its own column with bm25 weight 2.0 so a
  section heading match outranks a body-text match.
* All public functions are fail-open: an FTS error is logged and swallowed —
  the chunk store remains the source of truth and FTS must never break ingest.
* TTL reclamation: ``reclaim_expired`` drops FTS rows whose source documents
  were archived by ``source_store``.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from .cjk_fts import _cjk_expand, _match_query

logger = logging.getLogger(__name__)

_FTS_TABLE = "source_chunks_fts"
_FTS_META = "source_fts_meta"
_FTS_SCHEMA = "2"  # v2: char_start/char_end + raw display vs expanded search columns

# CJK FTS helpers now live in services/cjk_fts.py (shared with agent_memory,
# wiki/fts) and are re-exported here for backward compatibility.


def _session_factory() -> sessionmaker[Session]:
    from ..db.database import default_session_factory

    return default_session_factory()


def ensure_source_fts(session_factory: sessionmaker[Session] | None = None) -> None:
    """Create the FTS5 virtual table + schema meta if missing/outdated."""
    sf = session_factory or _session_factory()
    with sf() as session:
        session.execute(
            text(
                f"""CREATE TABLE IF NOT EXISTS {_FTS_META} (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                )"""
            )
        )
        cur = session.execute(
            text(f"SELECT v FROM {_FTS_META} WHERE k = 'schema'")
        ).scalar()
        if cur != _FTS_SCHEMA:
            session.execute(text(f"DROP TABLE IF EXISTS {_FTS_TABLE}"))
            session.execute(text(_fts_ddl()))
            session.execute(
                text(
                    f"""INSERT INTO {_FTS_META}(k, v) VALUES ('schema', :v)
                    ON CONFLICT(k) DO UPDATE SET v = excluded.v"""
                ),
                {"v": _FTS_SCHEMA},
            )
            session.commit()
            return
        session.execute(text(f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5("
                             + _fts_columns() + ", tokenize = 'unicode61')"))
        session.commit()


def _fts_columns() -> str:
    """Column list: raw display columns + expanded CJK search columns."""
    return (
        "chunk_id UNINDEXED, source_id UNINDEXED, page_no UNINDEXED, "
        "char_start UNINDEXED, char_end UNINDEXED, "
        "section_title, search_section, text, search_text"
    )


def _fts_ddl() -> str:
    return (
        f"CREATE VIRTUAL TABLE {_FTS_TABLE} USING fts5("
        f"{_fts_columns()}, tokenize = 'unicode61')"
    )


def index_source_chunks(
    source_id: str,
    rows: list[dict[str, Any]],
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """(Re)index one source's chunks. Idempotent. Fail-open; returns count."""
    if not source_id:
        return 0
    try:
        sf = session_factory or _session_factory()
        ensure_source_fts(sf)
        with sf() as session:
            session.execute(
                text(f"DELETE FROM {_FTS_TABLE} WHERE source_id = :sid"),
                {"sid": source_id},
            )
            n = 0
            for i, row in enumerate(rows or []):
                body = (row.get("text") or "")
                if not body.strip():
                    continue
                section = (row.get("heading_path") or "").strip()
                # char offsets: prefer char_start/char_end, fall back to the
                # chunk-store offset_start/offset_end naming.
                char_start = row.get("char_start")
                if char_start is None:
                    char_start = row.get("offset_start")
                char_end = row.get("char_end")
                if char_end is None:
                    char_end = row.get("offset_end")
                session.execute(
                    text(
                        f"""INSERT INTO {_FTS_TABLE}(
                            chunk_id, source_id, page_no, char_start, char_end,
                            section_title, search_section, text, search_text
                        ) VALUES (
                            :cid, :sid, :page, :cs, :ce,
                            :section, :search_section, :text, :search_text
                        )"""
                    ),
                    {
                        "cid": f"{source_id}#{i}",
                        "sid": source_id,
                        "page": row.get("page_no"),
                        "cs": char_start,
                        "ce": char_end,
                        "section": section[:2000],
                        "search_section": _cjk_expand(section[:2000]),
                        "text": body[:200_000],
                        "search_text": _cjk_expand(body[:200_000]),
                    },
                )
                n += 1
            session.commit()
            return n
    except Exception as exc:  # noqa: BLE001 — FTS must never break ingest
        logger.warning("source FTS index failed for %s: %s", source_id, exc)
        return 0


def search_chunks(
    query: str,
    *,
    source_ids: list[str] | None = None,
    limit: int = 20,
    session_factory: sessionmaker[Session] | None = None,
) -> list[dict[str, Any]]:
    """BM25 search over chunk FTS (search_section weight 2.0, search_text 1.0).

    Matches only the CJK-expanded search columns; display columns keep the
    raw text. Returns ``[{chunk_id, source_id, page_no, char_start, char_end,
    section_title, text, rank}]`` ordered by relevance (rank ascending).
    """
    match = _match_query(query)
    if not match:
        return []
    try:
        sf = session_factory or _session_factory()
        ensure_source_fts(sf)
        # Columns: chunk_id, source_id, page_no, char_start, char_end,
        #          section_title, search_section, text, search_text.
        # Only the two expanded search columns participate in MATCH/ranking.
        match_q = f"{{search_section search_text}} : {match}"
        sql = f"""SELECT chunk_id, source_id, page_no, char_start, char_end,
                         section_title, text,
                         bm25({_FTS_TABLE}, 0, 0, 0, 0, 0, 0, 2.0, 0, 1.0) AS rank
                  FROM {_FTS_TABLE}
                  WHERE {_FTS_TABLE} MATCH :q"""
        params: dict[str, Any] = {"q": match_q}
        if source_ids:
            placeholders = ", ".join(f":s{i}" for i in range(len(source_ids)))
            sql += f" AND source_id IN ({placeholders})"
            for i, sid in enumerate(source_ids):
                params[f"s{i}"] = sid
        sql += " ORDER BY rank LIMIT :lim"
        params["lim"] = max(1, min(int(limit), 200))
        with sf() as session:
            out = []
            for r in session.execute(text(sql), params):
                out.append(
                    {
                        "chunk_id": r[0],
                        "source_id": r[1],
                        "page_no": r[2],
                        "char_start": r[3],
                        "char_end": r[4],
                        "section_title": r[5],
                        "text": r[6],
                        "rank": r[7],
                    }
                )
            return out
    except Exception as exc:  # noqa: BLE001 — search degrades to empty
        logger.warning("source FTS search failed for %r: %s", query, exc)
        return []


def delete_source_chunks(
    source_id: str,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Drop all FTS rows for one source. Fail-open; returns rows deleted."""
    if not source_id:
        return 0
    try:
        sf = session_factory or _session_factory()
        ensure_source_fts(sf)
        with sf() as session:
            res = session.execute(
                text(f"DELETE FROM {_FTS_TABLE} WHERE source_id = :sid"),
                {"sid": source_id},
            )
            session.commit()
            return res.rowcount or 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("source FTS delete failed for %s: %s", source_id, exc)
        return 0


def reclaim_expired(
    days: int,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Drop FTS rows for sources archived longer than ``days`` (TTL reclamation)."""
    try:
        from ..db.source_store import get_source_store

        expired = get_source_store().list_archived_expired(days=days, limit=500)
        ids = [getattr(s, "id", None) or (s.get("id") if isinstance(s, dict) else None) for s in expired]
        n = 0
        for sid in ids:
            if sid:
                n += delete_source_chunks(sid, session_factory=session_factory)
        return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("source FTS reclaim failed: %s", exc)
        return 0
