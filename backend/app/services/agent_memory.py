"""Agent memory system (W2-4 / P1-1).

Long-lived memory across sessions in three scopes: ``global`` / ``project`` /
``user``.  Recall is FTS5 + bm25 with a character budget; writes are gated
against secrets and prompt-injection payloads and are idempotent via
``input_hash``.

Design notes:

* CJK handling mirrors ``services/wiki/fts.py`` and ``services/source_fts.py``:
  ``unicode61`` does not substring-match contiguous CJK, so indexed text is
  spaced per-character and MATCH queries are expanded the same way.
* Prompt injection point is :func:`build_memory_block`, which is disabled
  unless ``agent_memory_enabled`` is set on settings (read via
  ``getattr(..., False)`` — the setting is owned by the main flow, not this
  module).
* All public functions are fail-open: an unexpected error is logged and a
  safe default is returned, so memory can never break the main flow.
  Deliberate write-gate rejections raise :class:`MemoryRejected` instead.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings

logger = logging.getLogger(__name__)

_MEM_TABLE = "agent_memories"
_FTS_TABLE = "agent_memories_fts"
_FTS_META = "agent_memory_fts_meta"
_FTS_SCHEMA = "1"

_SCOPES = ("global", "project", "user")


class MemoryRejected(ValueError):
    """A remember() write was refused by the write gate (secret/injection)."""


# ---------------------------------------------------------------------------
# CJK helpers (copied pattern from services/wiki/fts.py, kept local on purpose)
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_SPECIAL = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


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


def _token_to_match(token: str) -> str | None:
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


# ---------------------------------------------------------------------------
# Write gate: secrets + prompt injection
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"sk-[A-Za-z0-9]{8,}",  # OpenAI-style keys
        r"AKIA[0-9A-Z]{16}",  # AWS access key id
        r"github_pat_[A-Za-z0-9_]+",
        r"gh[pousr]_[A-Za-z0-9]+",
        r"xox[bap]-",
        r"password\s*[:=]\s*\S+",
        r"passwd\s*[:=]\s*\S+",
        r"api[_-]?key\s*[:=]\s*\S+",
        r"secret\s*[:=]\s*\S+",
        r"token\s*[:=]\s*\S+",
    )
)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |the )?previous instructions",
        r"disregard (all |the |your )?(previous |prior )?instructions",
        r"forget (all |your )?instructions",
        r"override (the |your )?system",
        r"you are now ",
        r"system prompt",
        r"jailbreak",
        r"prompt injection",
        r"bypass (the |your |all )?(safety|filter|guardrail)",
    )
) + (
    re.compile(r"忽略(之前|以上|所有)的?(指令|指示|提示)"),
    re.compile(r"无视(系统|之前|以上)的?(指令|指示|提示)"),
    re.compile(r"忘记(之前|所有|你的)?(指令|指示)"),
    re.compile(r"你现在是"),
    re.compile(r"系统提示词"),
    re.compile(r"越狱"),
)


def _gate_check(key: str, value: str) -> None:
    blob = f"{key}\n{value}"
    for pat in _SECRET_PATTERNS:
        if pat.search(blob):
            raise MemoryRejected(f"write gate: secret-like content ({pat.pattern})")
    for pat in _INJECTION_PATTERNS:
        if pat.search(blob):
            raise MemoryRejected(f"write gate: injection-like content ({pat.pattern})")


# ---------------------------------------------------------------------------
# Session factory / schema
# ---------------------------------------------------------------------------


def _session_factory() -> sessionmaker[Session]:
    from ..db.database import default_session_factory

    return default_session_factory()


def ensure_agent_memory(session_factory: sessionmaker[Session] | None = None) -> None:
    """Create ``agent_memories`` + FTS5 tables if missing/outdated."""
    sf = session_factory or _session_factory()
    with sf() as session:
        session.execute(
            text(
                f"""CREATE TABLE IF NOT EXISTS {_MEM_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL DEFAULT '',
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    UNIQUE (scope, scope_id, key)
                )"""
            )
        )
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
            session.execute(
                text(
                    f"""CREATE VIRTUAL TABLE {_FTS_TABLE} USING fts5(
                        memory_id UNINDEXED,
                        key,
                        value,
                        tokenize = 'unicode61'
                    )"""
                )
            )
            # Re-index any rows that already exist (schema migration path).
            for (mid, k, v) in session.execute(
                text(f"SELECT id, key, value FROM {_MEM_TABLE}")
            ).all():
                session.execute(
                    text(
                        f"INSERT INTO {_FTS_TABLE} "
                        f"(memory_id, key, value) VALUES (:mid, :k, :v)"
                    ),
                    {"mid": mid, "k": _cjk_expand(k), "v": _cjk_expand(v)},
                )
            session.execute(
                text(
                    f"INSERT OR REPLACE INTO {_FTS_META} (k, v) "
                    f"VALUES ('schema', '{_FTS_SCHEMA}')"
                )
            )
        session.commit()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _input_hash(scope: str, scope_id: str, key: str, value: str) -> str:
    return hashlib.sha256(
        "\0".join([scope, scope_id, key, value]).encode("utf-8")
    ).hexdigest()


def _normalize(scope: str, scope_id: str | None) -> tuple[str, str]:
    if scope not in _SCOPES:
        raise ValueError(f"scope must be one of {_SCOPES}, got {scope!r}")
    sid = "" if scope == "global" else (scope_id or "")
    if scope != "global" and not sid:
        raise ValueError(f"scope_id is required for scope {scope!r}")
    return scope, sid


def _sync_fts(session: Session, memory_id: int, key: str, value: str) -> None:
    session.execute(
        text(f"DELETE FROM {_FTS_TABLE} WHERE memory_id = :mid"),
        {"mid": memory_id},
    )
    session.execute(
        text(
            f"INSERT INTO {_FTS_TABLE} (memory_id, key, value) "
            f"VALUES (:mid, :k, :v)"
        ),
        {"mid": memory_id, "k": _cjk_expand(key), "v": _cjk_expand(value)},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def remember(
    scope: str,
    scope_id: str | None,
    key: str,
    value: str,
    *,
    actor: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> dict[str, Any] | None:
    """Store a memory. Idempotent via ``input_hash``.

    Returns the stored row dict with ``changed`` True/False. Raises
    :class:`MemoryRejected` when the write gate fires. Unexpected errors are
    swallowed (fail-open) and return None.
    """
    try:
        scope, sid = _normalize(scope, scope_id)
        if not key or not key.strip():
            raise ValueError("key must be non-empty")
        _gate_check(key, value or "")
        sf = session_factory or _session_factory()
        ensure_agent_memory(sf)
        ihash = _input_hash(scope, sid, key.strip(), value or "")
        with sf() as session:
            row = session.execute(
                text(
                    f"SELECT id, value, input_hash, created_at, updated_at "
                    f"FROM {_MEM_TABLE} "
                    f"WHERE scope = :s AND scope_id = :sid AND key = :k"
                ),
                {"s": scope, "sid": sid, "k": key.strip()},
            ).mappings().first()
            now = _utcnow()
            if row and row["input_hash"] == ihash:
                return {
                    "id": row["id"], "scope": scope, "scope_id": sid,
                    "key": key.strip(), "value": row["value"],
                    "created_at": row["created_at"], "updated_at": row["updated_at"],
                    "changed": False,
                }
            if row:
                session.execute(
                    text(
                        f"UPDATE {_MEM_TABLE} SET value = :v, updated_at = :u, "
                        f"input_hash = :h WHERE id = :mid"
                    ),
                    {"v": value or "", "u": now, "h": ihash, "mid": row["id"]},
                )
                _sync_fts(session, row["id"], key.strip(), value or "")
                session.commit()
                return {
                    "id": row["id"], "scope": scope, "scope_id": sid,
                    "key": key.strip(), "value": value or "",
                    "created_at": row["created_at"], "updated_at": now,
                    "changed": True,
                }
            cur = session.execute(
                text(
                    f"INSERT INTO {_MEM_TABLE} "
                    f"(scope, scope_id, key, value, created_at, updated_at, input_hash) "
                    f"VALUES (:s, :sid, :k, :v, :c, :u, :h)"
                ),
                {"s": scope, "sid": sid, "k": key.strip(), "v": value or "",
                 "c": now, "u": now, "h": ihash},
            )
            mid = cur.lastrowid
            _sync_fts(session, mid, key.strip(), value or "")
            session.commit()
            return {
                "id": mid, "scope": scope, "scope_id": sid,
                "key": key.strip(), "value": value or "",
                "created_at": now, "updated_at": now, "changed": True,
            }
    except MemoryRejected:
        raise
    except (ValueError, LookupError):
        raise
    except Exception:
        logger.exception("agent_memory.remember failed (fail-open)")
        return None


def recall(
    query: str,
    *,
    scope: str,
    scope_id: str | None,
    budget_chars: int = 6000,
    limit: int = 20,
    session_factory: sessionmaker[Session] | None = None,
) -> list[dict[str, Any]]:
    """Recall memories via FTS5 + bm25, truncated to ``budget_chars``.

    ``global``-scope memories are always included alongside the requested
    scope. Returns [] on empty query or any error (fail-open).
    """
    try:
        scope, sid = _normalize(scope, scope_id)
        match = _match_query(query or "")
        if not match:
            return []
        sf = session_factory or _session_factory()
        ensure_agent_memory(sf)
        with sf() as session:
            rows = session.execute(
                text(
                    f"""SELECT m.scope, m.scope_id, m.key, m.value,
                              bm25({_FTS_TABLE}) AS rank
                       FROM {_FTS_TABLE} f
                       JOIN {_MEM_TABLE} m ON m.id = f.memory_id
                       WHERE {_FTS_TABLE} MATCH :q
                         AND ((m.scope = :s AND m.scope_id = :sid)
                              OR m.scope = 'global')
                       ORDER BY rank
                       LIMIT :lim"""
                ),
                {"q": match, "s": scope, "sid": sid, "lim": max(1, limit)},
            ).mappings().all()
        out: list[dict[str, Any]] = []
        used = 0
        for r in rows:
            item = {"scope": r["scope"], "scope_id": r["scope_id"],
                    "key": r["key"], "value": r["value"]}
            size = len(r["key"]) + len(r["value"]) + 4
            if used + size > budget_chars:
                if not out and budget_chars > 0:
                    # Even the top hit overflows: include it truncated.
                    room = max(0, budget_chars - len(r["key"]) - 8)
                    item["value"] = (r["value"][:room] + "…") if room < len(r["value"]) else r["value"]
                    item["truncated"] = True
                    out.append(item)
                break
            out.append(item)
            used += size
        return out
    except (ValueError, LookupError):
        raise
    except Exception:
        logger.exception("agent_memory.recall failed (fail-open)")
        return []


def forget(
    scope: str,
    scope_id: str | None,
    key: str,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> bool:
    """Delete a memory. Returns True if a row was removed (fail-open False)."""
    try:
        scope, sid = _normalize(scope, scope_id)
        sf = session_factory or _session_factory()
        ensure_agent_memory(sf)
        with sf() as session:
            mid = session.execute(
                text(
                    f"SELECT id FROM {_MEM_TABLE} "
                    f"WHERE scope = :s AND scope_id = :sid AND key = :k"
                ),
                {"s": scope, "sid": sid, "k": (key or "").strip()},
            ).scalar()
            if mid is None:
                return False
            session.execute(
                text(f"DELETE FROM {_FTS_TABLE} WHERE memory_id = :mid"),
                {"mid": mid},
            )
            session.execute(
                text(f"DELETE FROM {_MEM_TABLE} WHERE id = :mid"),
                {"mid": mid},
            )
            session.commit()
            return True
    except (ValueError, LookupError):
        raise
    except Exception:
        logger.exception("agent_memory.forget failed (fail-open)")
        return False


def _memory_enabled() -> bool:
    try:
        return bool(getattr(get_settings(), "agent_memory_enabled", False))
    except Exception:
        return False


def build_memory_block(
    scope: str,
    scope_id: str | None,
    query: str,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> str:
    """Build a ``## Memory`` prompt block, or "" when disabled/empty/error.

    This is the only integration point the main flow needs: it calls this and
    appends the result to the prompt. Fail-open by construction.
    """
    try:
        if not _memory_enabled():
            return ""
        hits = recall(query, scope=scope, scope_id=scope_id,
                      session_factory=session_factory)
        if not hits:
            return ""
        lines = ["## Memory"]
        for h in hits:
            lines.append(f"- {h['key']}: {h['value']}")
        return "\n".join(lines)
    except Exception:
        logger.exception("agent_memory.build_memory_block failed (fail-open)")
        return ""


__all__ = [
    "MemoryRejected",
    "build_memory_block",
    "ensure_agent_memory",
    "forget",
    "recall",
    "remember",
]
