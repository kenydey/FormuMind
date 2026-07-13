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

    limit = max_docs if max_docs is not None else get_settings().kb_ingest_max_docs
    targets: list[tuple[Evidence, str]] = []
    seen: set[str] = set()
    for ev in evidence:
        if len(targets) >= limit:
            break
        ident = (ev.identifier or "").strip()
        if not ident or ident in seen:
            continue
        kind = ff.classify(ev)
        if kind:
            targets.append((ev, kind))
            seen.add(ident)
    return targets


def _ingest_one(ev: Evidence, kind: str, timeout: float, emit: StatusCb, doc: dict[str, Any]) -> None:
    """Advance one document through the state machine (mutates *doc*)."""
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
        return

    doc["status"] = "fetching"
    emit(doc)
    try:
        text = ff._dispatch_fetch(kind, ev, timeout)
    except Exception as exc:
        text = degrade_return(logger, exc, f"kb_ingest fetch failed ({kind})", None)
    if not text:
        doc.update(status="failed", error="全文获取失败（无 OA 版本 / 下载超时 / 解析为空）")
        emit(doc)
        return

    doc["status"] = "indexing"
    emit(doc)
    source_id = ff._persist_fulltext(text, ev, kind)  # hash-dedup + chunk + embed inside
    if source_id:
        doc.update(status="indexed", source_id=source_id)
    else:
        doc.update(status="failed", error="入库失败（存储或索引异常）")
    emit(doc)


def ingest_evidence_docs(
    evidence: list[Evidence],
    *,
    max_docs: int | None = None,
    status_cb: StatusCb | None = None,
) -> dict[str, Any]:
    """Sequentially acquire + index the fetchable subset of *evidence*.

    Returns a summary dict (also the Celery task result):
    ``{"docs": [...], "total", "indexed", "skipped", "failed"}``.
    Never raises — one bad document must not kill the rest of the queue.
    """
    emit: StatusCb = status_cb or (lambda meta: None)
    timeout = float(get_settings().fulltext_timeout_s)
    targets = select_ingest_targets(evidence, max_docs=max_docs)

    docs = [_doc_meta(ev, kind) for ev, kind in targets]
    for doc in docs:  # announce the full queue up front
        emit(doc)

    for (ev, kind), doc in zip(targets, docs):
        try:
            _ingest_one(ev, kind, timeout, emit, doc)
        except Exception as exc:  # defensive: emit() itself must not kill the queue
            degrade_return(logger, exc, "kb_ingest document failed", None)
            doc.update(status="failed", error=str(exc)[:200])

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
