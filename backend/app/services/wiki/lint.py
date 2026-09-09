"""Lightweight wiki lint (W4) — stale / conflict / empty evidence."""
from __future__ import annotations

import json
import logging
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .schema import parse_front_matter

logger = logging.getLogger(__name__)


def _bounds_from_meta(meta: dict[str, Any]) -> list[dict[str, Any]]:
    raw = meta.get("bounds_json")
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    if isinstance(raw, list):
        return raw
    return []


def lint_page(path: str) -> list[str]:
    """Return flag labels for one page (does not persist)."""
    store = get_wiki_store()
    row = store.get_by_path(path)
    if row is None:
        return ["missing"]
    flags: list[str] = []
    if not (row.source_ids or []):
        flags.append("stale")
    md = store.read_markdown(path) or ""
    meta, body = parse_front_matter(md)
    if "## Evidence" not in body or "_No evidence" in body:
        if "stale" not in flags:
            flags.append("stale")
    # Conflict: overlapping bound names with inverted ranges on same page
    bounds = _bounds_from_meta(meta)
    by_name: dict[str, list[tuple[float | None, float | None]]] = {}
    for b in bounds:
        if not isinstance(b, dict):
            continue
        name = str(b.get("name") or "").strip().lower()
        if not name:
            continue
        lo = b.get("min", b.get("min_value"))
        hi = b.get("max", b.get("max_value"))
        try:
            lo_f = float(lo) if lo is not None else None
            hi_f = float(hi) if hi is not None else None
        except (TypeError, ValueError):
            continue
        by_name.setdefault(name, []).append((lo_f, hi_f))
        if lo_f is not None and hi_f is not None and lo_f > hi_f:
            flags.append("conflict")
    return list(dict.fromkeys(flags))


def lint_and_persist(path: str) -> list[str]:
    """Run lint and merge flags into page + disk front-matter."""
    settings = get_settings()
    if not settings.wiki_enabled:
        return []
    store = get_wiki_store()
    row = store.get_by_path(path)
    if row is None:
        return []
    found = lint_page(path)
    # Keep human_locked / existing non-lint flags
    keep = [f for f in (row.flags or []) if f not in {"stale", "conflict", "orphan", "missing"}]
    merged = list(dict.fromkeys([*keep, *found]))
    md = store.read_markdown(path) or ""
    meta, body = parse_front_matter(md)
    # Rebuild via upsert with same body content but updated flags in FM via dump — simpler:
    # rewrite flags line in front matter
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end > 0:
            header = md[3:end]
            rest = md[end + 4 :]
            lines = []
            replaced = False
            for ln in header.splitlines():
                if ln.strip().startswith("flags:"):
                    flag_list = ", ".join(f'"{f}"' for f in merged)
                    lines.append(f"flags: [{flag_list}]")
                    replaced = True
                else:
                    lines.append(ln)
            if not replaced:
                flag_list = ", ".join(f'"{f}"' for f in merged)
                lines.append(f"flags: [{flag_list}]")
            new_md = "---\n" + "\n".join(lines) + "\n---" + rest
            store.upsert_page(
                path=row.path,
                kind=row.kind,
                title=row.title or "",
                norm_key=row.norm_key or "",
                entity_id=row.entity_id,
                markdown=new_md,
                source_ids=list(row.source_ids or []),
                flags=merged,
            )
            return merged
    return found


def lint_paths(paths: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in paths:
        try:
            out[p] = lint_and_persist(p)
        except Exception as exc:  # noqa: BLE001
            logger.debug("wiki lint failed for %s: %s", p, exc)
            out[p] = []
    return out


def list_flagged_pages(*, limit: int = 100) -> list[dict[str, Any]]:
    store = get_wiki_store()
    rows = store.list_pages(limit=min(500, max(1, limit * 3)))
    flagged = []
    for row in rows:
        fl = list(row.flags or [])
        if not fl:
            continue
        flagged.append(
            {
                "id": row.id,
                "path": row.path,
                "kind": row.kind,
                "title": row.title,
                "flags": fl,
                "source_ids": list(row.source_ids or []),
            }
        )
        if len(flagged) >= limit:
            break
    return flagged
