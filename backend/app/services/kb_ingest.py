"""Async knowledge-base ingest queue — search results → full text → KB.

The search path streams abstract-level hits to the frontend immediately; this
module is the *background* half of the pipeline.  A dispatched ingest job
walks the fetchable evidence rows **one by one** (per-document status is more
useful to the UI than raw throughput, and sequential fetching is polite to
upstream sites):

    queued → fetching → indexing → indexed | skipped | failed | unsupported

Every transition is reported through ``status_cb`` so the Celery task can
publish SSE events; the frontend paints per-document badges from them.

Reuses the fulltext_fetcher registry (patent PDF / OA literature PDF / web
page) and the standard persistence path (``SourceDocument`` + ``kb_index``),
so parsing, chunking and embedding behave exactly like every other ingest
route.  Dedup is two-tier: ``origin_url`` before the download is attempted,
content hash afterwards (inside ``_persist_fulltext``).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ..config import get_settings
from ..domain.schemas import Evidence
from . import ingest_timing as timing
from .errors import degrade_return

logger = logging.getLogger(__name__)

# Terminal per-document states (anything else is transient).
TERMINAL_STATES = frozenset({"indexed", "skipped", "failed", "unsupported"})

StatusCb = Callable[[dict[str, Any]], None]


def ingest_enabled() -> bool:
    """Auto-dispatch switch: async KB build after search / research tasks."""
    settings = get_settings()
    return bool(settings.kb_ingest_auto and settings.kb_v2_enabled)


def _doc_meta(ev: Evidence, kind: str | None) -> dict[str, Any]:
    return {
        "identifier": ev.identifier,
        "title": (ev.title or ev.identifier)[:200],
        "kind": kind or "unsupported",
        "status": "queued",
        "source_id": None,
        "error": None,
    }


def select_ingest_targets(
    evidence: list[Evidence], *, max_docs: int | None = None
) -> list[tuple[Evidence, str]]:
    """The top fetchable rows in rank order, as (evidence, kind) pairs."""
    from . import fulltext_fetcher as ff

    # 0 (or None) means no cap: every fetchable hit is ingested. "Searched but
    # not stored" reads as data loss to whoever ran the search, so the default
    # is to store everything and let the operator opt into a limit.
    limit = max_docs if max_docs is not None else get_settings().kb_ingest_max_docs
    min_rel = get_settings().kb_ingest_min_relevance
    targets: list[tuple[Evidence, str]] = []
    seen: set[str] = set()
    for ev in evidence:
        if limit and len(targets) >= limit:
            break
        if min_rel > 0 and (ev.relevance or 0) < min_rel:
            continue
        ident = (ev.identifier or "").strip()
        if not ident or ident in seen:
            continue
        kind = ff.classify(ev)
        if kind:
            targets.append((ev, kind))
            seen.add(ident)
    return targets


def _fetch_one(
    ev: Evidence, kind: str, timeout: float, emit: StatusCb, doc: dict[str, Any]
) -> str | None:
    """Acquire one document's full text. Network-bound; safe to run in parallel.

    Returns the text, or None when the document is already known (status
    ``skipped``) or unobtainable (status ``failed``). Mutates *doc*.
    """
    from ..db.source_store import get_source_store
    from . import fulltext_fetcher as ff

    ident = (ev.identifier or "").strip()

    # Dedup tier 1: this URL / patent id / DOI was already acquired.
    try:
        existing = get_source_store().find_by_origin_url(ident)
    except Exception as exc:
        existing = degrade_return(logger, exc, "kb_ingest dedup lookup failed", None)
    if existing is not None:
        doc.update(status="skipped", source_id=existing.id)
        emit(doc)
        return None

    doc["status"] = "fetching"
    emit(doc)
    try:
        with timing.span("fetch"):
            text = ff._dispatch_fetch(kind, ev, timeout)
    except Exception as exc:
        text = degrade_return(logger, exc, f"kb_ingest fetch failed ({kind})", None)
    # Recorded even when empty: "how much did the web channel actually return"
    # is the question, and a zero is as much of an answer as a large number.
    timing.note(chars=len(text or ""))
    if not text:
        doc.update(status="failed", error="全文获取失败（无 OA 版本 / 下载超时 / 解析为空）")
        emit(doc)
        return None
    return text


def _index_one(
    text: str,
    ev: Evidence,
    kind: str,
    emit: StatusCb,
    doc: dict[str, Any],
    *,
    project_id: str | None = None,
) -> None:
    """Chunk, embed and persist one document. Kept serial — SQLite writers."""
    from . import fulltext_fetcher as ff

    doc["status"] = "indexing"
    emit(doc)
    source_id = ff._persist_fulltext(text, ev, kind, project_id=project_id)  # hash-dedup + chunk + embed inside
    if source_id:
        doc.update(status="indexed", source_id=source_id)
    else:
        doc.update(status="failed", error="入库失败（存储或索引异常）")
    emit(doc)


def _ingest_one(ev: Evidence, kind: str, timeout: float, emit: StatusCb, doc: dict[str, Any], *, project_id: str | None = None) -> None:
    """Advance one document through the state machine (mutates *doc*)."""
    text = _fetch_one(ev, kind, timeout, emit, doc)
    if text:
        _index_one(text, ev, kind, emit, doc, project_id=project_id)


def ingest_evidence_docs(
    evidence: list[Evidence],
    *,
    max_docs: int | None = None,
    status_cb: StatusCb | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Sequentially acquire + index the fetchable subset of *evidence*.

    Returns a summary dict (also the Celery task result):
    ``{"docs": [...], "total", "indexed", "skipped", "failed"}``.
    Never raises — one bad document must not kill the rest of the queue.
    """
    import concurrent.futures

    settings = get_settings()
    emit: StatusCb = status_cb or (lambda meta: None)
    timeout = float(settings.fulltext_timeout_s)
    targets = select_ingest_targets(evidence, max_docs=max_docs)

    docs = [_doc_meta(ev, kind) for ev, kind in targets]
    for doc in docs:  # announce the full queue up front
        emit(doc)

    # Fetch concurrently, index serially, and pipeline the two. Fetching is
    # network-bound and is the whole cost of a large batch — 340 documents one
    # at a time, at a 20 s timeout each, is over an hour. Indexing stays on one
    # thread because it writes SQLite and because a fetch may run the PDF
    # parser cascade, whose peak (~350 MB, or ~557 MB when a scan goes through
    # OCR) is what bounds the worker count on a small host — not the sockets.
    workers = max(1, int(getattr(settings, "kb_ingest_workers", 3)))

    def _fetch(i: int) -> tuple[int, str | None]:
        ev, kind = targets[i]
        try:
            # `track` spans threads: this runs in a worker, the matching index
            # phase runs on the main thread, and both land in one record.
            with timing.track(ev.identifier or "", kind):
                return i, _fetch_one(ev, kind, timeout, emit, docs[i])
        except Exception as exc:
            degrade_return(logger, exc, "kb_ingest fetch failed", None)
            docs[i].update(status="failed", error=str(exc)[:200])
            return i, None

    def _index(i: int, text: str | None) -> None:
        ev, kind = targets[i]
        if text:
            try:
                with timing.track(ev.identifier or "", kind):
                    _index_one(text, ev, kind, emit, docs[i], project_id=project_id)
            except Exception as exc:  # one bad document must not kill the queue
                degrade_return(logger, exc, "kb_ingest document failed", None)
                docs[i].update(status="failed", error=str(exc)[:200])
        timing.finish(ev.identifier or "", docs[i]["status"])

    with timing.batch("kb_ingest"):
        if workers == 1 or len(targets) <= 1:
            for i in range(len(targets)):
                _index(*_fetch(i))
        else:
            # `as_completed`, not `ex.map`: map returns an iterator that yields
            # in submission order, so one slow download at the head held back
            # every document behind it and nothing was indexed until the last
            # fetch finished. It also meant every fetched full text — up to
            # several hundred documents — sat in memory at once waiting for
            # the indexing phase to start.
            #
            # Indexing now happens in completion order. Content-hash dedup
            # therefore resolves to whichever of two byte-identical documents
            # finished first rather than to the higher-ranked one; the stored
            # text is the same either way, only the recorded origin_url differs.
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_fetch, i): i for i in range(len(targets))}
                for fut in concurrent.futures.as_completed(futures):
                    try:
                        idx, text = fut.result()
                    except Exception as exc:  # _fetch catches, but never trust that
                        idx = futures[fut]
                        degrade_return(logger, exc, "kb_ingest fetch crashed", None)
                        docs[idx].update(status="failed", error=str(exc)[:200])
                        text = None
                    _index(idx, text)

    summary = {
        "docs": docs,
        "total": len(docs),
        "indexed": sum(1 for d in docs if d["status"] == "indexed"),
        "skipped": sum(1 for d in docs if d["status"] == "skipped"),
        "failed": sum(1 for d in docs if d["status"] == "failed"),
    }
    logger.info(
        "kb_ingest: %d indexed / %d skipped / %d failed of %d",
        summary["indexed"], summary["skipped"], summary["failed"], summary["total"],
    )
    return summary
