"""Phase 3: embed Wiki page summaries into existing ``document_chunks``.

Reuses the KB embedding pipeline (no second vector DB). Each wiki page becomes
a ``SourceDocument(source_kind=\"wiki\")`` with one summary chunk. Chat Track A
retrieves these via semantic/keyword search; Track B (``search_chunks``)
excludes them so Raw RAG stays dual-track. Claims still filter ``source==\"wiki\"``.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from ...config import get_settings
from ...db.models import SourceDocument
from ...db.wiki_store import get_wiki_store
from ...domain.schemas import Evidence
from .schema import parse_front_matter

logger = logging.getLogger(__name__)

_WIKI_ORIGIN_PREFIX = "wiki://"
_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # URL namespace


def wiki_source_id(path: str) -> str:
    """Stable SourceDocument id for a wiki path (uuid5)."""
    rel = (path or "").replace("\\", "/").lstrip("/")
    return str(uuid.uuid5(_NS, f"formumind-wiki:{rel}"))


def wiki_origin_url(path: str) -> str:
    rel = (path or "").replace("\\", "/").lstrip("/")
    return f"{_WIKI_ORIGIN_PREFIX}{rel}"


def path_from_origin(origin_url: str | None) -> str | None:
    raw = (origin_url or "").strip()
    if raw.startswith(_WIKI_ORIGIN_PREFIX):
        return raw[len(_WIKI_ORIGIN_PREFIX) :]
    return None


def _summary_blob(
    *,
    path: str,
    title: str,
    kind: str,
    norm_key: str,
    flags: list[str] | None,
    markdown: str,
) -> str:
    meta, body = parse_front_matter(markdown or "")
    summary = ""
    if "## Summary" in body:
        summary = body.split("## Summary", 1)[1]
        if "## " in summary:
            summary = summary.split("## ", 1)[0]
        summary = summary.strip()
    if not summary:
        summary = str(meta.get("summary") or "").strip()
    if not summary:
        summary = body.strip()[:1200]
    flag_blob = ",".join(flags or [])
    parts = [
        f"Wiki/{kind}: {title}",
        f"path: {path}",
        f"norm_key: {norm_key}" if norm_key else "",
        f"flags: {flag_blob}" if flag_blob else "",
        summary,
    ]
    return "\n".join(p for p in parts if p).strip()[:4000]


def embed_wiki_page(path: str) -> dict[str, Any]:
    """Upsert one wiki page summary into document_chunks. No-op when flag off."""
    settings = get_settings()
    if not settings.wiki_enabled or not getattr(settings, "wiki_embed_enabled", False):
        return {"ok": False, "skipped": True, "reason": "wiki_embed_disabled"}

    rel = (path or "").replace("\\", "/").lstrip("/")
    if not rel:
        return {"ok": False, "error": "empty_path"}

    store = get_wiki_store()
    row = store.get_by_path(rel)
    md = store.read_markdown(rel) or ""
    if row is None and not md:
        return {"ok": False, "error": "page_not_found", "path": rel}

    title = (row.title if row else "") or rel
    kind = (row.kind if row else "") or "page"
    norm_key = (row.norm_key if row else "") or ""
    flags = list(row.flags or []) if row else []
    blob = _summary_blob(
        path=rel,
        title=title,
        kind=kind,
        norm_key=norm_key,
        flags=flags,
        markdown=md,
    )
    if not blob:
        return {"ok": False, "error": "empty_summary", "path": rel}

    sid = wiki_source_id(rel)
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()
    origin = wiki_origin_url(rel)

    embedding = None
    emb_model = None
    try:
        from ..kb_index import _embed_model_name, _embed_texts

        emb_model = _embed_model_name()
        vecs = _embed_texts([blob], emb_model)
        if vecs and vecs[0]:
            embedding = vecs[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("wiki embed vectors unavailable for %s: %s", rel, exc)

    from ...db.chunk_store import get_chunk_store
    from ...db.database import default_session_factory
    from ...db.session_utils import commit_session

    session_factory = default_session_factory()
    with commit_session(session_factory) as session:
        doc = session.get(SourceDocument, sid)
        if doc is None:
            session.add(
                SourceDocument(
                    id=sid,
                    filename=rel,
                    title=title[:512],
                    source_kind="wiki",
                    content_hash=digest,
                    origin_url=origin[:1024],
                    full_text=blob,
                    raw_text_chars=len(blob),
                    extraction_status="skipped",
                )
            )
        else:
            doc.filename = rel
            doc.title = title[:512]
            doc.source_kind = "wiki"
            doc.content_hash = digest
            doc.origin_url = origin[:1024]
            doc.full_text = blob
            doc.raw_text_chars = len(blob)

        get_chunk_store().replace_for_source_in(
            session,
            sid,
            [
                {
                    "text": blob,
                    "heading_path": f"wiki/{kind}",
                    "meta": {
                        "wiki": True,
                        "wiki_path": rel,
                        "wiki_kind": kind,
                        "wiki_norm_key": norm_key,
                    },
                    "embedding": embedding,
                    "embedding_model": emb_model,
                }
            ],
        )

    try:
        get_chunk_store().bump_generation()
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "path": rel,
        "source_id": sid,
        "embedded": embedding is not None,
        "chars": len(blob),
    }


def rebuild_all_wiki_embeds() -> dict[str, Any]:
    """Re-embed all wiki pages. Returns ``{ok, indexed, embedded_vectors}``."""
    settings = get_settings()
    if not settings.wiki_enabled:
        return {"ok": False, "reason": "wiki_disabled", "indexed": 0}
    if not getattr(settings, "wiki_embed_enabled", False):
        return {"ok": False, "reason": "wiki_embed_disabled", "indexed": 0}

    store = get_wiki_store()
    pages = store.list_pages(limit=500)
    indexed = 0
    with_vec = 0
    for row in pages:
        out = embed_wiki_page(row.path)
        if out.get("ok"):
            indexed += 1
            if out.get("embedded"):
                with_vec += 1
    return {"ok": True, "indexed": indexed, "embedded_vectors": with_vec}


def list_wiki_source_ids() -> set[str]:
    """SourceDocument ids with ``source_kind=wiki`` (for Track B exclusion)."""
    from ...db.database import default_session_factory

    ids: set[str] = set()
    with default_session_factory()() as session:
        rows = (
            session.query(SourceDocument.id)
            .filter(SourceDocument.source_kind == "wiki")
            .all()
        )
        for (sid,) in rows:
            ids.add(sid)
    return ids


def search_wiki_embedded(query: str, *, k: int = 5) -> list[Evidence]:
    """Semantic (or keyword) search over wiki summary chunks only."""
    settings = get_settings()
    if not settings.wiki_enabled or not getattr(settings, "wiki_embed_enabled", False):
        return []
    q = (query or "").strip()
    if not q:
        return []

    from ...db.chunk_store import get_chunk_store
    from ...db.database import default_session_factory

    wiki_meta: dict[str, dict[str, str]] = {}
    with default_session_factory()() as session:
        rows = (
            session.query(SourceDocument)
            .filter(SourceDocument.source_kind == "wiki")
            .all()
        )
        for r in rows:
            path = path_from_origin(r.origin_url) or r.filename or ""
            wiki_meta[r.id] = {
                "path": path,
                "title": r.title or path,
                "kind": "page",
            }

    if not wiki_meta:
        return []

    chunks = [
        c for c in get_chunk_store().all_chunks(limit=2000) if c.source_id in wiki_meta
    ]
    if not chunks:
        return []

    scored: list[tuple[float, Any, dict[str, str]]] = []
    q_vec = None
    try:
        from ..kb_index import _embed_model_name, _embed_texts

        model = _embed_model_name()
        vecs = _embed_texts([q], model)
        if vecs and vecs[0]:
            q_vec = vecs[0]
    except Exception:  # noqa: BLE001
        q_vec = None

    q_lower = q.lower()
    q_tokens = {t for t in q_lower.replace("/", " ").split() if t}

    for c in chunks:
        score = 0.0
        if q_vec and c.embedding and len(c.embedding) == len(q_vec):
            score = float(sum(a * b for a, b in zip(q_vec, c.embedding)))
        else:
            text = (c.text or "").lower()
            hits = sum(1 for t in q_tokens if t in text)
            score = hits / max(1, len(q_tokens)) * 0.5
        meta = dict(wiki_meta.get(c.source_id) or {})
        blob = f"{meta.get('title', '')} {meta.get('path', '')}".lower()
        if any(t in blob for t in q_tokens if len(t) >= 2):
            score += 0.05
        cm = c.meta or {}
        if cm.get("wiki_kind"):
            meta["kind"] = str(cm.get("wiki_kind"))
        if cm.get("wiki_path"):
            meta["path"] = str(cm["wiki_path"])
        if score > 0.05:
            scored.append((score, c, meta))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[Evidence] = []
    seen: set[str] = set()
    for score, chunk, meta in scored[: max(1, min(20, k))]:
        path = meta.get("path") or ""
        if not path or path in seen:
            continue
        seen.add(path)
        kind = meta.get("kind") or "page"
        title = meta.get("title") or path
        out.append(
            Evidence(
                source="wiki",
                identifier=f"wiki:{path}",
                title=f"[Wiki/{kind}] {title}",
                snippet=(chunk.text or "")[:800],
                relevance=max(0.05, min(1.0, round(float(score), 4))),
                url=None,
            )
        )
    return out
