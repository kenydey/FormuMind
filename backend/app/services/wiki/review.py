"""Reserved human-review contract for Wiki pages (Q4).

Not a WYSIWYG editor — only toggles ``reviewed`` / ``human_override`` in
front-matter and syncs the ``unreviewed`` flag. Full authoring remains deferred.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ...db.wiki_store import get_wiki_store
from .schema import parse_front_matter, utcnow_iso

logger = logging.getLogger(__name__)

_REVIEWED_RE = re.compile(r"(?m)^reviewed:\s*.*$")
_OVERRIDE_RE = re.compile(r"(?m)^human_override:\s*.*$")
_FLAGS_RE = re.compile(r"(?m)^flags:\s*\[(.*)\]\s*$")


def _yaml_escape(s: str) -> str:
    t = (s or "").replace("\n", " ").strip()
    if any(c in t for c in ":#{}[],&*?|>!%@`'\"\\"):
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return t or '""'


def _set_or_insert(header: str, key: str, value: str, pattern: re.Pattern[str]) -> str:
    line = f"{key}: {value}"
    if pattern.search(header):
        return pattern.sub(line, header, count=1)
    # Insert before trailing blank / end
    return header.rstrip() + "\n" + line + "\n"


def _sync_unreviewed_flag(header: str, *, reviewed: bool) -> str:
    m = _FLAGS_RE.search(header)
    if not m:
        if reviewed:
            return header
        return header.rstrip() + '\nflags: ["unreviewed"]\n'
    inner = m.group(1).strip()
    parts = [p.strip().strip('"').strip("'") for p in inner.split(",") if p.strip()]
    parts = [p for p in parts if p]
    if reviewed:
        parts = [p for p in parts if p != "unreviewed"]
    elif "unreviewed" not in parts:
        parts.append("unreviewed")
    rendered = ", ".join(f'"{p}"' for p in parts)
    return _FLAGS_RE.sub(f"flags: [{rendered}]", header, count=1)


def apply_page_review(
    path: str,
    *,
    reviewed: bool | None = None,
    human_override: str | None = None,
) -> dict[str, Any]:
    """Update review fields on an existing wiki page. Raises ``FileNotFoundError``."""
    store = get_wiki_store()
    rel = (path or "").replace("\\", "/").lstrip("/")
    row = store.get_by_path(rel)
    md = store.read_markdown(rel) or ""
    if row is None or not md:
        raise FileNotFoundError(rel)

    if not md.startswith("---"):
        raise ValueError("page has no front-matter; cannot reserve review fields")

    end = md.find("\n---", 3)
    if end < 0:
        raise ValueError("malformed front-matter")
    header = md[3:end]
    body = md[end + 4 :]  # includes leading newline after closing ---

    if reviewed is not None:
        header = _set_or_insert(
            header, "reviewed", "true" if reviewed else "false", _REVIEWED_RE
        )
        header = _sync_unreviewed_flag(header, reviewed=reviewed)

    if human_override is not None:
        header = _set_or_insert(
            header, "human_override", _yaml_escape(human_override), _OVERRIDE_RE
        )

    header = _set_or_insert(header, "updated_at", utcnow_iso(), re.compile(r"(?m)^updated_at:\s*.*$"))

    new_md = f"---\n{header.strip()}\n---{body}"
    meta, _ = parse_front_matter(new_md)
    flags = meta.get("flags") or []
    if isinstance(flags, str):
        flags = [flags]
    if reviewed is True:
        flags = [f for f in flags if f != "unreviewed"]
    elif reviewed is False and "unreviewed" not in flags:
        flags = [*flags, "unreviewed"]

    store.upsert_page(
        path=rel,
        kind=row.kind,
        title=row.title or rel,
        norm_key=row.norm_key or "",
        entity_id=row.entity_id,
        markdown=new_md,
        source_ids=list(row.source_ids or []),
        flags=list(flags),
        replace_source_ids=True,
    )
    return {
        "ok": True,
        "path": rel,
        "reviewed": bool(meta.get("reviewed") is True or str(meta.get("reviewed")).lower() == "true")
        if reviewed is not None
        else None,
        "human_override": human_override,
        "flags": list(flags),
    }
