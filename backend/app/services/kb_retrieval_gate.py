"""Rule-only quality gate for KB hybrid / probe / recommend fuse **and** ingest.

Literature retrieval already runs ``content_filter.filter_evidence`` at the
federated merge point. Chunks that somehow land in ``document_chunks`` (manual
upload, soft ingest miss, etc.) used to bypass that stack when scored by
``hybrid_search_scored``. This module applies a *chunk-native* subset of the
same rules so Hub retrieval probe and recommend hybrid fuse stay aligned.

The same blocked-domain / garbage-text rules also run at ``index_source`` so
dirty sources never become ``document_chunks`` — not only get filtered from
top-k. Wiki exclusion applies at *retrieval* only; wiki must still be able to
ingest.

Process-local drop counters (``record_gate_drop`` / ``gate_drop_stats``) feed
the Hub retrieval probe and ``GET /api/kb/stats`` so operators can see how
often the gate fires. Not durable across restarts.

LLM judge / SimHash / substrate checks are intentionally out of scope.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Iterable, Literal
from urllib.parse import urlparse

from ..config import get_settings
from .content_filter import DEFAULT_BLOCKED_DOMAINS, _WORD_RE

logger = logging.getLogger(__name__)

GateStage = Literal["retrieval", "ingest"]
GateReason = Literal["blocked_domain", "garbage_snippet", "wiki_track"]

_REASONS: tuple[GateReason, ...] = ("blocked_domain", "garbage_snippet", "wiki_track")
_STAGES: tuple[GateStage, ...] = ("retrieval", "ingest")

_lock = threading.Lock()
_DROP_COUNTS: dict[str, int] = {
    f"{stage}.{reason}": 0 for stage in _STAGES for reason in _REASONS
}


def _empty_stage_bucket() -> dict[str, int]:
    return {r: 0 for r in _REASONS}


def record_gate_drop(stage: GateStage, reason: str, n: int = 1) -> None:
    """Increment process-local counters. Unknown reason/stage is ignored."""
    if n <= 0:
        return
    if stage not in _STAGES:
        return
    if reason not in _REASONS:
        return
    # Ingest never applies wiki_track.
    if stage == "ingest" and reason == "wiki_track":
        return
    key = f"{stage}.{reason}"
    with _lock:
        _DROP_COUNTS[key] = int(_DROP_COUNTS.get(key, 0)) + int(n)


def gate_drop_stats() -> dict[str, dict[str, int]]:
    """Lifetime counters nested as ``{retrieval|ingest: {reason: n}}``."""
    with _lock:
        snap = dict(_DROP_COUNTS)
    out: dict[str, dict[str, int]] = {s: _empty_stage_bucket() for s in _STAGES}
    for key, val in snap.items():
        stage, _, reason = key.partition(".")
        if stage in out and reason in out[stage]:
            out[stage][reason] = int(val)
    return out


def gate_drop_snapshot() -> dict[str, int]:
    """Flat ``stage.reason → n`` copy for before/after delta."""
    with _lock:
        return dict(_DROP_COUNTS)


def gate_drop_delta(before: dict[str, int], after: dict[str, int] | None = None) -> dict[str, dict[str, int]]:
    """Nested delta between two flat snapshots (default after = current)."""
    end = after if after is not None else gate_drop_snapshot()
    out: dict[str, dict[str, int]] = {s: _empty_stage_bucket() for s in _STAGES}
    for key in _DROP_COUNTS:
        stage, _, reason = key.partition(".")
        if stage not in out or reason not in out[stage]:
            continue
        delta = int(end.get(key, 0)) - int(before.get(key, 0))
        if delta > 0:
            out[stage][reason] = delta
    return out


def reset_gate_drop_stats() -> None:
    """Test helper — zero all counters."""
    with _lock:
        for key in list(_DROP_COUNTS):
            _DROP_COUNTS[key] = 0


@dataclass(frozen=True)
class _SourceGateMeta:
    origin_url: str | None = None
    source_kind: str | None = None


def _blocked_domains() -> tuple[str, ...]:
    extra = get_settings().content_filter_blocked_domains or []
    return DEFAULT_BLOCKED_DOMAINS + tuple(d.strip().lower() for d in extra if d.strip())


def is_blocked_origin_url(url: str | None) -> bool:
    """True when ``url`` host matches the content-filter blocklist."""
    raw = (url or "").strip()
    if not raw.lower().startswith(("http://", "https://")):
        return False
    try:
        host = (urlparse(raw).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in _blocked_domains())


def is_garbage_chunk_text(text: str, *, min_chars: int | None = None) -> bool:
    """Chunk-body analogue of literature garbage-snippet detection.

    Literature filters ``title + snippet`` (often >40 chars). Stored chunks can
    be shorter technical paragraphs, so the default floor is softer
    (``max(20, min_snippet_chars // 2)``) unless the caller overrides.
    """
    settings = get_settings()
    if min_chars is None:
        base = int(getattr(settings, "content_filter_min_snippet_chars", 40) or 40)
        limit = max(20, base // 2)
    else:
        limit = int(min_chars)
    body = (text or "").strip()
    if len(body) < limit:
        return True
    word_chars = len(_WORD_RE.findall(body))
    return word_chars / max(1, len(body)) < 0.4


def _load_source_meta(source_ids: Iterable[str]) -> dict[str, _SourceGateMeta]:
    ids = [sid for sid in dict.fromkeys(source_ids) if sid]
    if not ids:
        return {}
    try:
        from ..db.models import SourceDocument
        from ..db.source_store import get_source_store

        out: dict[str, _SourceGateMeta] = {}
        with get_source_store()._session_factory() as session:
            rows = (
                session.query(
                    SourceDocument.id,
                    SourceDocument.origin_url,
                    SourceDocument.source_kind,
                )
                .filter(SourceDocument.id.in_(ids))
                .all()
            )
        for sid, origin_url, source_kind in rows:
            out[str(sid)] = _SourceGateMeta(
                origin_url=origin_url,
                source_kind=source_kind,
            )
        return out
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("kb retrieval gate: source meta load failed: %s", exc)
        return {}


def _wiki_source_ids() -> set[str]:
    try:
        from .wiki.embed import list_wiki_source_ids

        return set(list_wiki_source_ids() or [])
    except Exception:
        return set()


def drop_reason_for_chunk(
    chunk,
    *,
    source_meta: dict[str, _SourceGateMeta] | None = None,
    wiki_ids: set[str] | None = None,
) -> str | None:
    """Return a drop reason string, or None if the chunk may stay."""
    settings = get_settings()
    if not bool(getattr(settings, "content_filter_enabled", True)):
        return None

    sid = getattr(chunk, "source_id", None) or ""
    wiki = wiki_ids if wiki_ids is not None else _wiki_source_ids()
    if sid and sid in wiki:
        return "wiki_track"

    meta = (source_meta or {}).get(sid) if sid else None
    if meta and is_blocked_origin_url(meta.origin_url):
        return "blocked_domain"

    text = getattr(chunk, "text", "") or ""
    if is_garbage_chunk_text(text):
        return "garbage_snippet"

    return None


def gate_chunk_indices(
    chunks: list,
    order: list[int],
    *,
    top_k: int,
) -> list[int]:
    """Walk score-ordered indices and keep until ``top_k`` survivors.

    When the content filter is disabled, returns ``order[:top_k]``.
    """
    if top_k <= 0:
        return []
    settings = get_settings()
    if not bool(getattr(settings, "content_filter_enabled", True)):
        return order[:top_k]

    source_ids = [getattr(chunks[i], "source_id", "") for i in order if 0 <= i < len(chunks)]
    meta = _load_source_meta(source_ids)
    wiki_ids = _wiki_source_ids()
    kept: list[int] = []
    dropped = 0
    for i in order:
        if i < 0 or i >= len(chunks):
            continue
        reason = drop_reason_for_chunk(chunks[i], source_meta=meta, wiki_ids=wiki_ids)
        if reason:
            dropped += 1
            record_gate_drop("retrieval", reason)
            continue
        kept.append(i)
        if len(kept) >= top_k:
            break
    if dropped:
        logger.debug("kb retrieval gate dropped %d chunk(s) before top_k=%d", dropped, top_k)
    return kept


def ingest_block_reason_for_source(source_id: str | None) -> str | None:
    """Return ``blocked_domain`` when this source must not write any chunks.

    Returns None when the content filter is off, meta is missing, or the
    origin is clean. Wiki is intentionally not blocked here.
    """
    settings = get_settings()
    if not bool(getattr(settings, "content_filter_enabled", True)):
        return None
    sid = (source_id or "").strip()
    if not sid:
        return None
    meta = _load_source_meta([sid]).get(sid)
    if meta and is_blocked_origin_url(meta.origin_url):
        return "blocked_domain"
    return None


def gate_ingest_rows(
    rows: list[dict],
    *,
    source_id: str | None = None,
) -> tuple[list[dict], str | None]:
    """Filter chunk rows before ``replace_for_source``.

    Returns ``(kept_rows, drop_reason)``. When the whole source is blocked,
    ``kept_rows`` is empty and ``drop_reason`` is ``blocked_domain``. When only
    garbage chunks are removed, ``drop_reason`` is ``garbage_snippet`` if
    nothing remains, else None (partial keep). Disabled filter → identity.
    """
    settings = get_settings()
    if not bool(getattr(settings, "content_filter_enabled", True)):
        return rows, None

    block = ingest_block_reason_for_source(source_id)
    if block:
        record_gate_drop("ingest", block)
        logger.info(
            "kb ingest gate: source %s blocked (%s) — writing 0 chunks",
            source_id,
            block,
        )
        return [], block

    kept: list[dict] = []
    garbage_n = 0
    for row in rows:
        text = (row.get("text") if isinstance(row, dict) else "") or ""
        if is_garbage_chunk_text(text):
            garbage_n += 1
            continue
        kept.append(row)
    if garbage_n:
        record_gate_drop("ingest", "garbage_snippet", garbage_n)
    if garbage_n and not kept:
        logger.info(
            "kb ingest gate: source %s all %d chunk(s) garbage — writing 0",
            source_id,
            garbage_n,
        )
        return [], "garbage_snippet"
    if garbage_n:
        logger.debug(
            "kb ingest gate: source %s dropped %d garbage chunk(s), kept %d",
            source_id,
            garbage_n,
            len(kept),
        )
    return kept, None
