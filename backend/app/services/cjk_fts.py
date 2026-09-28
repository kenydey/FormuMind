"""Shared CJK-aware FTS5 helpers.

Single source of truth for the unicode61-friendly CJK tokenization used by
``agent_memory.py``, ``source_fts.py`` and ``wiki/fts.py`` (code review
2026-09-28, R-1: the four helpers used to be copied verbatim in all three
modules and had already started to diverge).

The tokenizers are the more complete of the two historical variants:
``[\\w\\u4e00-\\u9fff]+`` is a strict superset of the old wiki-only
``[a-zA-Z0-9\\u4e00-\\u9fff]`` (``\\w`` additionally covers ``_`` and other
Unicode word characters).
"""

from __future__ import annotations

import re

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


def flush_latin(buf: list[str], pieces: list[str]) -> None:
    """Flush a pending run of non-CJK chars into quoted FTS5 phrase pieces.

    Module-level extraction of the ``flush_latin`` closure that used to be
    nested inside ``_token_to_match`` in each of the three call sites.
    """
    if not buf:
        return
    safe = _SPECIAL.sub(" ", "".join(buf)).strip()
    buf.clear()
    if safe:
        pieces.append(f'"{safe}"')


def _token_to_match(token: str) -> str | None:
    """Turn one user token into an FTS5 expression (CJK → per-char AND)."""
    pieces: list[str] = []
    buf: list[str] = []
    for ch in token:
        if _is_cjk(ch):
            flush_latin(buf, pieces)
            pieces.append(f'"{ch}"')
        else:
            buf.append(ch)
    flush_latin(buf, pieces)
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
