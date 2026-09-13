"""SQLite FTS5 index for Wiki pages (Phase 2).

Indexes a CJK-aware ``search`` blob (title / kind / norm_key / flags / body)
while keeping the original ``body`` for snippets. Synced on every
``WikiStore.upsert_page``.

SQLite ``unicode61`` does not substring-match contiguous CJK; we space CJK
characters at index time and expand multi-char CJK queries to ``AND`` of
single-character tokens.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

_FTS_TABLE = "wiki_pages_fts"
_FTS_META = "wiki_fts_meta"
_FTS_SCHEMA = "2"
_TOKEN = re.compile(r"[a-zA-Z0-9\u4e00-\u9fff]{1,}")
_SPECIAL = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _cjk_expand(s: str) -> str:
    """Insert spaces around CJK so unicode61 tokenizes each character."""
    parts: list[str] = []
    for ch in s or "":
        if _is_cjk(ch):
            parts.append(f" {ch} ")
        else:
            parts.append(ch)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def ensure_fts(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {_FTS_META} (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                )
                """
            )
        )
        cur = session.execute(
            text(f"SELECT v FROM {_FTS_META} WHERE k = 'schema'")
        ).scalar()
        if cur != _FTS_SCHEMA:
            session.execute(text(f"DROP TABLE IF EXISTS {_FTS_TABLE}"))
            session.execute(
                text(
                    f"""
                    CREATE VIRTUAL TABLE {_FTS_TABLE} USING fts5(
                        path UNINDEXED,
                        title UNINDEXED,
                        kind UNINDEXED,
                        norm_key UNINDEXED,
                        flags UNINDEXED,
                        body UNINDEXED,
                        search,
                        tokenize = 'unicode61'
                    )
                    """
                )
            )
            session.execute(
                text(
                    f"""
                    INSERT INTO {_FTS_META}(k, v) VALUES ('schema', :v)
                    ON CONFLICT(k) DO UPDATE SET v = excluded.v
                    """
                ),
                {"v": _FTS_SCHEMA},
            )
            session.commit()
            return

        # Schema already current — still ensure virtual table exists.
        session.execute(
            text(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5(
                    path UNINDEXED,
                    title UNINDEXED,
                    kind UNINDEXED,
                    norm_key UNINDEXED,
                    flags UNINDEXED,
                    body UNINDEXED,
                    search,
                    tokenize = 'unicode61'
                )
                """
            )
        )
        session.commit()


def _strip_body(markdown: str) -> str:
    raw = markdown or ""
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end >= 0:
            return raw[end + 4 :].lstrip("\n")
    return raw


def index_page(
    session_factory: sessionmaker[Session],
    *,
    path: str,
    title: str,
    kind: str,
    norm_key: str,
    flags: list[str] | None,
    markdown: str,
) -> None:
    try:
        ensure_fts(session_factory)
        body = _strip_body(markdown)
        flag_blob = " ".join(flags or [])
        search_blob = _cjk_expand(
            " ".join(
                [
                    title or "",
                    kind or "",
                    norm_key or "",
                    flag_blob,
                    path or "",
                    body,
                ]
            )
        )
        with session_factory() as session:
            session.execute(
                text(f"DELETE FROM {_FTS_TABLE} WHERE path = :path"),
                {"path": path},
            )
            session.execute(
                text(
                    f"""
                    INSERT INTO {_FTS_TABLE}(
                        path, title, kind, norm_key, flags, body, search
                    )
                    VALUES (
                        :path, :title, :kind, :norm_key, :flags, :body, :search
                    )
                    """
                ),
                {
                    "path": path,
                    "title": title or "",
                    "kind": kind or "",
                    "norm_key": norm_key or "",
                    "flags": flag_blob,
                    "body": body[:200_000],
                    "search": search_blob[:200_000],
                },
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 — FTS must never break compile
        logger.warning("wiki FTS index failed for %s: %s", path, exc)


def rebuild_all(session_factory: sessionmaker[Session], store: Any) -> int:
    """Reindex all wiki pages from store. Returns count."""
    # Force schema migration + empty index before refill.
    with session_factory() as session:
        session.execute(text(f"DELETE FROM {_FTS_META} WHERE k = 'schema'"))
        session.commit()
    ensure_fts(session_factory)
    pages = store.list_pages(limit=500)
    n = 0
    for row in pages:
        md = store.read_markdown(row.path) or ""
        index_page(
            session_factory,
            path=row.path,
            title=row.title or "",
            kind=row.kind or "",
            norm_key=row.norm_key or "",
            flags=list(row.flags or []),
            markdown=md,
        )
        n += 1
    return n


def _token_to_match(token: str) -> str | None:
    """Turn one user token into an FTS5 expression (CJK → per-char AND)."""
    pieces: list[str] = []
    buf: list[str] = []

    def flush_latin() -> None:
        if not buf:
            return
        safe = _SPECIAL.sub(" ", "".join(buf)).strip()
        buf.clear()
        if safe:
            pieces.append(f'"{safe}"')

    for ch in token:
        if _is_cjk(ch):
            flush_latin()
            pieces.append(f'"{ch}"')
        else:
            buf.append(ch)
    flush_latin()
    if not pieces:
        return None
    return " AND ".join(pieces)


def _match_query(q: str) -> str | None:
    tokens = [t for t in _TOKEN.findall(q or "") if t.strip()]
    if not tokens:
        return None
    parts: list[str] = []
    for t in tokens[:12]:
        expr = _token_to_match(t)
        if expr:
            parts.append(f"({expr})" if " AND " in expr else expr)
    if not parts:
        return None
    return " AND ".join(parts)


def search_fts(
    session_factory: sessionmaker[Session],
    query: str,
    *,
    kind: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return ``[{path, title, kind, norm_key, snippet, rank}]``."""
    match = _match_query(query)
    if not match:
        return []
    try:
        ensure_fts(session_factory)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wiki FTS ensure failed: %s", exc)
        return []

    # body is UNINDEXED (readable CJK); search column is what MATCH uses.
    sql = f"""
        SELECT path, title, kind, norm_key, body,
               snippet({_FTS_TABLE}, 5, '[', ']', '…', 16) AS snip,
               bm25({_FTS_TABLE}) AS rank
        FROM {_FTS_TABLE}
        WHERE {_FTS_TABLE} MATCH :q
    """
    params: dict[str, Any] = {"q": match, "limit": min(200, max(1, limit))}
    if kind:
        sql += " AND kind = :kind"
        params["kind"] = kind
    sql += " ORDER BY rank LIMIT :limit"

    try:
        with session_factory() as session:
            rows = session.execute(text(sql), params).mappings().all()
        out: list[dict[str, Any]] = []
        for r in rows:
            body = r.get("body") or ""
            snip = _manual_snippet(body, query) or (r["snip"] or "").strip()
            if not snip:
                snip = (r["title"] or "")[:120]
            out.append(
                {
                    "path": r["path"],
                    "title": r["title"] or "",
                    "kind": r["kind"] or "",
                    "norm_key": r["norm_key"] or "",
                    "snippet": snip[:400],
                    "rank": float(r["rank"] or 0),
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("wiki FTS search failed: %s", exc)
        return []


def _manual_snippet(body: str, query: str, *, radius: int = 40) -> str:
    """Build a readable snippet with ``[hit]`` markers (CJK-safe)."""
    if not body:
        return ""
    tokens = [t for t in _TOKEN.findall(query or "") if t]
    lower = body.lower()
    hit_at = -1
    hit_len = 0
    for t in tokens:
        idx = lower.find(t.lower())
        if idx >= 0:
            hit_at = idx
            hit_len = len(t)
            break
    if hit_at < 0 and tokens:
        # CJK: locate first character of first token
        ch = tokens[0][0]
        idx = body.find(ch)
        if idx >= 0:
            hit_at = idx
            hit_len = len(tokens[0]) if tokens[0] in body[idx : idx + len(tokens[0]) + 2] else 1
            if tokens[0] in body:
                hit_at = body.find(tokens[0])
                hit_len = len(tokens[0])
    if hit_at < 0:
        return body[:120].replace("\n", " ").strip()
    lo = max(0, hit_at - radius)
    hi = min(len(body), hit_at + hit_len + radius)
    chunk = body[lo:hi].replace("\n", " ")
    mid = hit_at - lo
    marked = f"{chunk[:mid]}[{chunk[mid : mid + hit_len]}]{chunk[mid + hit_len :]}"
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(body) else ""
    return f"{prefix}{marked}{suffix}".strip()
