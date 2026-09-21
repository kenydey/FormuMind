"""S4: save Chat / Deep Research answers as L2 Wiki drafts.

Path prefix ``queries/`` (App-maintained drafts). Flags always include
``unreviewed`` + ``draft``. Never writes L1 bounds; never Claims evidence.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .schema import dump_page, query_draft_path, safe_key, utcnow_iso

logger = logging.getLogger(__name__)

DRAFT_KIND = "query"
DRAFT_PATH_PREFIX = "queries/"
_MAX_BODY = 80_000


def is_query_draft_path(path: str | None) -> bool:
    p = (path or "").replace("\\", "/").lstrip("/")
    return p.startswith(DRAFT_PATH_PREFIX) or p.startswith("themes/draft-")


def _title_from(*, title: str | None, question: str) -> str:
    t = (title or "").strip()
    if t:
        return t[:200]
    q = (question or "").strip().replace("\n", " ")
    if not q:
        return "Chat 草稿"
    return (q[:80] + ("…" if len(q) > 80 else "")) or "Chat 草稿"


def _slug_from(title: str, question: str) -> str:
    base = safe_key(title or question or "note", limit=60)
    if base and base != "u-" + hashlib.sha1(b"unknown").hexdigest()[:12]:
        return base
    digest = hashlib.sha1((question or title or "note").encode()).hexdigest()[:10]
    return f"note-{digest}"


def _source_ids_from_citations(citations: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for c in citations or []:
        if not isinstance(c, dict):
            continue
        for key in ("source_id", "id", "identifier"):
            v = c.get(key)
            if v and isinstance(v, str) and not v.startswith("wiki:"):
                # Prefer real source UUIDs; skip wiki: identifiers
                if key == "identifier" and ":" in v and not re.match(
                    r"^[0-9a-f-]{36}$", v, re.I
                ):
                    continue
                out.append(v)
                break
    return list(dict.fromkeys(out))[:40]


def save_chat_draft(
    *,
    project_id: str,
    question: str = "",
    answer_markdown: str,
    title: str | None = None,
    source_ids: list[str] | None = None,
    citations: list[dict[str, Any]] | None = None,
    origin: str = "chat",
) -> dict[str, Any]:
    """Persist an L2 query draft. Raises PermissionError when flag/wiki off."""
    settings = get_settings()
    if not settings.wiki_enabled:
        raise PermissionError("wiki_enabled is false")
    if not getattr(settings, "wiki_chat_save_draft", False):
        raise PermissionError("wiki_chat_save_draft is false")

    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id required")
    body = (answer_markdown or "").strip()
    if not body:
        raise ValueError("answer_markdown required")
    if len(body) > _MAX_BODY:
        body = body[:_MAX_BODY] + "\n\n_…truncated…_"

    page_title = _title_from(title=title, question=question)
    slug = _slug_from(page_title, question)
    path = query_draft_path(project_id, slug)

    sids = list(source_ids or [])
    sids.extend(_source_ids_from_citations(citations))
    sids = list(dict.fromkeys(sids))[:40]

    evidence_blocks = [
        f"### Origin `{origin}` · project `{pid}`",
        "",
        "### Question",
        (question or "_（无问题文本）_").strip(),
        "",
        "### Answer",
        body,
        "",
        "> L2 draft · `unreviewed` · **not Claims evidence** · DOE must ignore `queries/`.",
    ]
    summary = (question or page_title)[:400]
    markdown = dump_page(
        kind=DRAFT_KIND,
        title=page_title,
        entity_id=f"query:{safe_key(pid)}:{slug}"[:64],
        norm_key=slug,
        source_ids=sids,
        flags=["unreviewed", "draft"],
        summary=summary,
        evidence_blocks=evidence_blocks,
        extra_meta={
            "template": "chat_draft",
            "project_id": pid,
            "origin": origin,
            "disclaimer": "draft_not_claims",
            "llm_overwrite": "forbidden",
        },
    )

    store = get_wiki_store()
    store.upsert_page(
        path=path,
        kind=DRAFT_KIND,
        title=page_title,
        norm_key=slug,
        entity_id=f"query:{safe_key(pid)}:{slug}"[:64],
        markdown=markdown,
        source_ids=sids,
        flags=["unreviewed", "draft"],
        replace_source_ids=True,
    )
    return {
        "ok": True,
        "path": path,
        "title": page_title,
        "kind": DRAFT_KIND,
        "flags": ["unreviewed", "draft"],
        "project_id": pid,
        "disclaimer": "draft_not_claims",
        "updated_at": utcnow_iso(),
    }
