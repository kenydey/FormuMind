"""Wiki keyword retrieve for Chat dual-track (W3)."""
from __future__ import annotations

import re
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from ...domain.schemas import Evidence
from .schema import parse_front_matter

_TOKEN = re.compile(r"[a-zA-Z0-9\u4e00-\u9fff]{2,}")


def is_wiki_evidence(ev: Evidence | dict[str, Any]) -> bool:
    if isinstance(ev, dict):
        src = str(ev.get("source") or "")
        ident = str(ev.get("identifier") or "")
    else:
        src = ev.source or ""
        ident = ev.identifier or ""
    return src == "wiki" or ident.startswith("wiki:")


def filter_raw_evidence(evidence: list[Evidence]) -> list[Evidence]:
    """Claims / provenance stay on Raw chunks only."""
    return [e for e in evidence if not is_wiki_evidence(e)]


def _normalize_chat_mode(raw: str | None) -> str:
    mode = (raw or "balanced").strip().lower()
    if mode in ("wiki_first", "wiki", "wiki-priority"):
        return "wiki_first"
    if mode in ("raw_first", "raw", "raw-priority"):
        return "raw_first"
    return "balanced"


def search_wiki(query: str, *, k: int = 5) -> list[Evidence]:
    """Keyword search over wiki titles / norm_key / markdown body."""
    settings = get_settings()
    if not settings.wiki_enabled or not settings.wiki_chat_blend:
        return []
    if _normalize_chat_mode(getattr(settings, "wiki_chat_mode", None)) == "raw_first":
        k = min(k, 2)
    q = (query or "").strip()
    if not q:
        return []
    tokens = [t.lower() for t in _TOKEN.findall(q)]
    if not tokens:
        tokens = [q.lower()]

    store = get_wiki_store()
    pages = store.list_pages(limit=min(500, max(50, k * 40)))
    scored: list[tuple[float, Evidence]] = []
    for row in pages:
        md = store.read_markdown(row.path) or ""
        meta, body = parse_front_matter(md)
        title = (row.title or meta.get("title") or row.path).strip()
        blob = f"{title}\n{row.norm_key}\n{row.path}\n{body}".lower()
        hits = sum(1 for t in tokens if t in blob)
        if hits <= 0:
            continue
        summary = ""
        if "## Summary" in body:
            summary = body.split("## Summary", 1)[1]
            if "## " in summary:
                summary = summary.split("## ", 1)[0]
            summary = summary.strip()
        snippet = (summary or body)[:800].strip()
        if not snippet:
            continue
        score = min(
            1.0,
            0.35
            + 0.15 * hits
            + (0.1 if row.kind in ("material", "system", "pitfall") else 0)
            + (0.08 if row.kind == "theme" and str(row.path or "").startswith("themes/project-") else 0),
        )
        scored.append(
            (
                score,
                Evidence(
                    source="wiki",
                    identifier=f"wiki:{row.path}",
                    title=f"[Wiki/{row.kind}] {title}",
                    snippet=snippet,
                    relevance=score,
                    url=None,
                ),
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    keyword_hits = [ev for _, ev in scored[: max(1, min(20, k))]]

    # Phase 3: merge embedded wiki summaries (semantic) when flag on.
    if getattr(settings, "wiki_embed_enabled", False):
        try:
            from .embed import search_wiki_embedded

            embedded = search_wiki_embedded(q, k=k)
        except Exception:
            embedded = []
        if embedded:
            by_id: dict[str, Evidence] = {ev.identifier: ev for ev in embedded}
            for ev in keyword_hits:
                prev = by_id.get(ev.identifier)
                if prev is None or float(ev.relevance or 0) > float(prev.relevance or 0):
                    by_id[ev.identifier] = ev
            merged = sorted(
                by_id.values(),
                key=lambda e: float(e.relevance or 0),
                reverse=True,
            )
            return merged[: max(1, min(20, k))]
    return keyword_hits


def blend_wiki_evidence(
    question: str,
    sources: list[Evidence],
    *,
    k: int | None = None,
) -> tuple[list[Evidence], int]:
    """Merge wiki hits (deduped). Ordering depends on ``wiki_chat_mode``.

    Returns (merged, wiki_added_count). Claims still use ``filter_raw_evidence``.
    """
    settings = get_settings()
    if not settings.wiki_enabled or not settings.wiki_chat_blend:
        return sources, 0
    mode = _normalize_chat_mode(getattr(settings, "wiki_chat_mode", None))
    if mode == "raw_first":
        default_k = min(2, max(1, settings.kb_chat_top_k // 8 or 1))
    elif mode == "wiki_first":
        default_k = min(8, max(3, settings.kb_chat_top_k // 2 or 3))
    else:
        default_k = min(5, max(2, settings.kb_chat_top_k // 4 or 2))
    top_k = k if k is not None else default_k
    hits = search_wiki(question, k=top_k)
    if not hits:
        return sources, 0
    seen = {ev.identifier for ev in sources}
    added = [h for h in hits if h.identifier not in seen]
    if not added:
        return sources, 0
    if mode == "raw_first":
        return sources + added, len(added)
    return added + sources, len(added)
