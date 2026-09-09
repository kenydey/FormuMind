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


def search_wiki(query: str, *, k: int = 5) -> list[Evidence]:
    """Keyword search over wiki titles / norm_key / markdown body."""
    settings = get_settings()
    if not settings.wiki_enabled or not settings.wiki_chat_blend:
        return []
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
        # Prefer summary section
        summary = ""
        if "## Summary" in body:
            summary = body.split("## Summary", 1)[1]
            if "## " in summary:
                summary = summary.split("## ", 1)[0]
            summary = summary.strip()
        snippet = (summary or body)[:800].strip()
        if not snippet:
            continue
        score = min(1.0, 0.35 + 0.15 * hits + (0.1 if row.kind in ("material", "system", "pitfall") else 0))
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
    return [ev for _, ev in scored[: max(1, min(20, k))]]


def blend_wiki_evidence(
    question: str,
    sources: list[Evidence],
    *,
    k: int | None = None,
) -> tuple[list[Evidence], int]:
    """Prepend wiki hits (deduped). Returns (merged, wiki_added_count)."""
    settings = get_settings()
    if not settings.wiki_enabled or not settings.wiki_chat_blend:
        return sources, 0
    top_k = k if k is not None else min(5, max(2, settings.kb_chat_top_k // 4 or 2))
    hits = search_wiki(question, k=top_k)
    if not hits:
        return sources, 0
    seen = {ev.identifier for ev in sources}
    added = [h for h in hits if h.identifier not in seen]
    if not added:
        return sources, 0
    # Wiki first so prompt sections can separate cleanly; raw follows.
    return added + sources, len(added)
