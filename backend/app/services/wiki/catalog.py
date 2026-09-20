"""Deterministic Wiki catalog (S2) — App-maintained index from ``wiki_pages``.

Borrowed role from llm_wiki ``index.md``: full-library map for L2 navigation.
Regenerated from DB; never a second SSOT; never LLM-overwritten.
"""
from __future__ import annotations

import logging
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .schema import utcnow_iso

logger = logging.getLogger(__name__)

CATALOG_REL_PATH = "catalog.md"
# Derived artifact paths — never listed as catalog entries.
_SKIP_PATHS = frozenset({"catalog.md", "index.md", "log.md", "overview.md"})


def list_catalog_entries(
    *,
    limit: int = 500,
    kinds: list[str] | None = None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return catalog rows sorted by (kind, path) for stable rebuild equality."""
    store = get_wiki_store()
    kind_filter = None
    if kinds and len(kinds) == 1:
        kind_filter = kinds[0]
    rows = store.list_pages(kind=kind_filter, limit=min(500, max(1, limit)), offset=0)
    if kinds and len(kinds) > 1:
        allow = {k.strip().lower() for k in kinds if k and k.strip()}
        rows = [r for r in rows if (r.kind or "").lower() in allow]
    if project_id:
        from .project_scope import filter_wiki_rows

        rows = filter_wiki_rows(rows, project_id)

    entries: list[dict[str, Any]] = []
    for r in rows:
        path = (r.path or "").replace("\\", "/").lstrip("/")
        if not path or path in _SKIP_PATHS:
            continue
        entries.append(
            {
                "path": path,
                "kind": (r.kind or "").strip() or "unknown",
                "title": (r.title or "").strip() or path,
                "norm_key": (r.norm_key or "").strip(),
                "flags": list(r.flags or []),
                "source_ids": list(r.source_ids or []),
            }
        )
    entries.sort(key=lambda e: (e["kind"].lower(), e["path"].lower()))
    return entries[: max(1, limit)]


def render_catalog_markdown(
    entries: list[dict[str, Any]],
    *,
    generated_at: str | None = None,
    project_id: str | None = None,
) -> str:
    """Render deterministic Markdown (stable ordering; no LLM prose)."""
    ts = generated_at or utcnow_iso()
    lines: list[str] = [
        "---",
        "kind: catalog",
        "title: Wiki Catalog",
        f"generated_at: {ts}",
        "app_maintained: true",
        "llm_overwrite: forbidden",
        f"entry_count: {len(entries)}",
    ]
    if project_id:
        lines.append(f"project_id: {project_id}")
    lines.extend(
        [
            "---",
            "",
            "# Wiki Catalog",
            "",
            "> App-generated index from `wiki_pages` (S2). "
            "**Do not LLM-overwrite.** Rebuild: `POST /api/wiki/catalog/rebuild`.",
            "",
        ]
    )
    if not entries:
        lines.append("_No wiki pages yet._")
        lines.append("")
        return "\n".join(lines)

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_kind.setdefault(e["kind"], []).append(e)

    for kind in sorted(by_kind.keys(), key=str.lower):
        group = by_kind[kind]
        lines.append(f"## {kind} ({len(group)})")
        lines.append("")
        for e in group:
            flags = e.get("flags") or []
            flag_s = f" · flags: {', '.join(flags)}" if flags else ""
            nk = e.get("norm_key") or ""
            link = f"[[{kind}:{nk}|{e['title']}]]" if nk else f"[[{e['title']}]]"
            lines.append(f"- {link} (`{e['path']}`){flag_s}")
        lines.append("")
    return "\n".join(lines)


def build_catalog(
    *,
    limit: int = 500,
    kinds: list[str] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Build catalog payload from DB (does not write disk)."""
    settings = get_settings()
    if not settings.wiki_enabled:
        raise PermissionError("wiki_enabled is false")
    generated_at = utcnow_iso()
    entries = list_catalog_entries(limit=limit, kinds=kinds, project_id=project_id)
    markdown = render_catalog_markdown(
        entries, generated_at=generated_at, project_id=project_id
    )
    return {
        "ok": True,
        "path": CATALOG_REL_PATH,
        "generated_at": generated_at,
        "entry_count": len(entries),
        "entries": entries,
        "markdown": markdown,
        "persisted": False,
    }


def rebuild_catalog(
    *,
    persist: bool = True,
    limit: int = 500,
    kinds: list[str] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Rebuild catalog; optionally write ``catalog.md`` under wiki root (not wiki_pages)."""
    out = build_catalog(limit=limit, kinds=kinds, project_id=project_id)
    if not persist:
        return out
    store = get_wiki_store()
    root = store.root()
    root.mkdir(parents=True, exist_ok=True)
    abs_path = (root / CATALOG_REL_PATH).resolve()
    if not str(abs_path).startswith(str(root.resolve())):
        raise ValueError("catalog path escapes wiki root")
    abs_path.write_text(out["markdown"], encoding="utf-8")
    out["persisted"] = True
    out["disk_path"] = str(abs_path)
    return out


def catalog_snippet_for_compile(*, limit: int = 40) -> str:
    """Short deterministic catalog block for optional L2 theme injection."""
    entries = list_catalog_entries(limit=limit)
    if not entries:
        return "_Wiki catalog empty._"
    lines: list[str] = [
        "_Injected from App-maintained catalog (not LLM). Full map: `catalog.md`._",
        "",
    ]
    for e in entries:
        nk = e.get("norm_key") or ""
        title = e.get("title") or e["path"]
        kind = e.get("kind") or "page"
        if nk:
            lines.append(f"- [[{kind}:{nk}|{title}]] (`{e['path']}`)")
        else:
            lines.append(f"- [[{title}]] (`{e['path']}`)")
    return "\n".join(lines)
