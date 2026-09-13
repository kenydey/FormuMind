"""L2 Theme compiler — system overview (Phase 2).

Flag-gated (``wiki_llm_themes_enabled``, default False). Deterministic skeleton
from L1 pages; optional LLM narrative when the flag is on and the LLM succeeds.
Never writes L1 bounds; never feeds Claims / DOE hard bounds.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .schema import (
    dump_page,
    parse_front_matter,
    safe_key,
    system_path,
    theme_path,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

THEME_TEMPLATE = "system_overview"


def compile_theme(
    *,
    system_key: str | None = None,
    topic: str | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Compile a system-overview theme page under ``themes/``.

    Returns ``{ok, path, ...}``. Raises ``PermissionError`` when flag is off
    (API maps to 409).
    """
    settings = get_settings()
    if not settings.wiki_enabled:
        raise PermissionError("wiki_enabled is false")
    if not getattr(settings, "wiki_llm_themes_enabled", False):
        raise PermissionError("wiki_llm_themes_enabled is false")

    key = safe_key(system_key or topic or "")
    if not key:
        return {"ok": False, "error": "system_key or topic required"}

    store = get_wiki_store()
    sys_rel = system_path(key)
    sys_md = store.read_markdown(sys_rel) or ""
    sys_meta, sys_body = parse_front_matter(sys_md)
    sys_row = store.get_by_path(sys_rel)

    # Collect related L1 pages by keyword overlap in title/norm_key.
    related = []
    for row in store.list_pages(limit=200):
        if row.kind == "theme":
            continue
        blob = f"{row.title} {row.norm_key} {row.path}".lower()
        if key in blob or key.replace("-", "") in blob.replace("-", ""):
            related.append(row)

    source_ids: list[str] = []
    if sys_row:
        source_ids.extend(list(sys_row.source_ids or []))
    for r in related:
        source_ids.extend(list(r.source_ids or []))
    source_ids = list(dict.fromkeys(source_ids))

    l1_links = []
    if sys_row or sys_md:
        l1_links.append(f"- [[system:{key}|{sys_meta.get('title') or key}]] (`{sys_rel}`)")
    for r in related[:20]:
        if r.path == sys_rel:
            continue
        l1_links.append(f"- [[{r.kind}:{r.norm_key or r.path}|{r.title or r.path}]] (`{r.path}`)")

    bounds_block = ""
    if sys_meta.get("bounds_json"):
        bounds_block = f"```json\n{sys_meta.get('bounds_json')}\n```"
    elif "## " in sys_body:
        # Pull a short excerpt from the system page body
        bounds_block = sys_body[:1200].strip()

    narrative = ""
    llm_generated = False
    model_name = ""
    prompt_hash = ""
    if use_llm:
        narrative, model_name, prompt_hash = _try_llm_narrative(
            key=key,
            title=str(sys_meta.get("title") or key),
            body_excerpt=sys_body[:3000],
            related_titles=[r.title for r in related[:8]],
        )
        llm_generated = bool(narrative)

    if not narrative:
        narrative = (
            f"本页为体系 **{sys_meta.get('title') or key}** 的编译综述骨架（确定性）。"
            f"以下内容聚合自 L1 `systems/` 与相关材料/机理/避坑页；"
            f"数值与边界以 L1 为准，本页不覆盖 L1。"
        )

    title = f"{sys_meta.get('title') or key} · 体系综述"
    evidence_blocks = [
        "### L1 anchors",
        *(l1_links or ["_No related L1 pages yet._"]),
        "",
        "### Bounds / windows (from L1)",
        bounds_block or "_No bounds on L1 system page._",
        "",
        "### Narrative",
        narrative.strip(),
    ]

    extra = {
        "template": THEME_TEMPLATE,
        "llm_generated": "true" if llm_generated else "false",
        "reviewed": "false",
        "model": model_name or "",
        "prompt_hash": prompt_hash or "",
        "l1_system_path": sys_rel,
    }
    markdown = dump_page(
        kind="theme",
        title=title,
        entity_id=f"theme:system:{key}"[:64],
        norm_key=key,
        source_ids=source_ids,
        flags=["unreviewed"] if not llm_generated else ["unreviewed", "llm_draft"],
        summary=narrative[:400],
        evidence_blocks=evidence_blocks,
        extra_meta=extra,
    )
    path = theme_path(key)
    store.upsert_page(
        path=path,
        kind="theme",
        title=title,
        norm_key=key,
        entity_id=f"theme:system:{key}"[:64],
        markdown=markdown,
        source_ids=source_ids,
        flags=["unreviewed"],
        replace_source_ids=True,
    )
    return {
        "ok": True,
        "path": path,
        "title": title,
        "llm_generated": llm_generated,
        "source_ids": source_ids,
        "l1_links": len(l1_links),
        "template": THEME_TEMPLATE,
        "updated_at": utcnow_iso(),
    }


def _try_llm_narrative(
    *,
    key: str,
    title: str,
    body_excerpt: str,
    related_titles: list[str],
) -> tuple[str, str, str]:
    """Best-effort LLM narrative; returns (text, model, prompt_hash) or empty."""
    try:
        from pydantic import BaseModel, Field

        from ..llm import complete_structured

        class ThemeNarrative(BaseModel):
            overview: str = Field(description="2-4 paragraph industrial overview in Chinese")
            pitfalls: str = Field(default="", description="Key pitfalls / process cautions")

        system = (
            "You are an industrial formulation chemist writing a concise system overview "
            "for a compiled wiki. Use ONLY the provided L1 excerpts. Do not invent numbers. "
            "Reply in Chinese. Keep claims qualitative unless the excerpt states a number."
        )
        user = (
            f"System key: {key}\nTitle: {title}\n"
            f"Related L1: {', '.join(related_titles) or '(none)'}\n\n"
            f"L1 excerpt:\n{body_excerpt or '(empty)'}\n"
        )
        prompt_hash = hashlib.sha1(f"{system}\n{user}".encode()).hexdigest()[:16]
        model_name = ""
        try:
            from ...config import get_settings

            model_name = getattr(get_settings(), "llm_model", "") or ""
        except Exception:
            pass
        out, err = complete_structured(system, user, ThemeNarrative, retry=False)
        if out is None:
            logger.info("theme LLM narrative skipped: %s", err)
            return "", model_name, prompt_hash
        text = out.overview.strip()
        if out.pitfalls.strip():
            text += "\n\n**避坑要点：** " + out.pitfalls.strip()
        return text, model_name, prompt_hash
    except Exception as exc:  # noqa: BLE001
        logger.info("theme LLM unavailable: %s", exc)
        return "", "", ""
