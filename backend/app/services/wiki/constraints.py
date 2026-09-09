"""Wiki → DOE parameter bounds / forbidden zones (W4)."""
from __future__ import annotations

import json
import logging
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .schema import parse_front_matter

logger = logging.getLogger(__name__)


def _parse_bounds(meta: dict[str, Any]) -> list[dict[str, Any]]:
    raw = meta.get("bounds_json") or meta.get("bounds")
    if isinstance(raw, list):
        return [b for b in raw if isinstance(b, dict)]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [b for b in data if isinstance(b, dict)]
        except json.JSONDecodeError:
            return []
    return []


def _parse_forbidden(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("forbidden_json") or meta.get("forbidden")
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x) for x in data if str(x).strip()]
        except json.JSONDecodeError:
            return [raw.strip()]
    return []


def wiki_parameter_bounds(*, limit: int = 200) -> list[dict[str, Any]]:
    """Flatten bounds from all wiki pages. Empty when DOE wiki flag off."""
    settings = get_settings()
    if not settings.wiki_enabled or not settings.wiki_doe_constraints:
        return []
    store = get_wiki_store()
    out: list[dict[str, Any]] = []
    try:
        pages = store.list_pages(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("wiki bounds list failed: %s", exc)
        return []
    for row in pages:
        md = store.read_markdown(row.path) or ""
        meta, _ = parse_front_matter(md)
        for b in _parse_bounds(meta):
            name = str(b.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "min": b.get("min", b.get("min_value")),
                    "max": b.get("max", b.get("max_value")),
                    "unit": str(b.get("unit") or ""),
                    "path": row.path,
                    "title": row.title or name,
                }
            )
    return out


def wiki_forbidden(*, limit: int = 200) -> list[dict[str, str]]:
    settings = get_settings()
    if not settings.wiki_enabled or not settings.wiki_doe_constraints:
        return []
    store = get_wiki_store()
    out: list[dict[str, str]] = []
    try:
        pages = store.list_pages(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("wiki forbidden list failed: %s", exc)
        return []
    for row in pages:
        md = store.read_markdown(row.path) or ""
        meta, _ = parse_front_matter(md)
        for text in _parse_forbidden(meta):
            out.append({"text": text, "path": row.path, "title": row.title or row.path})
        if row.kind == "pitfall" and row.title:
            out.append(
                {
                    "text": row.title,
                    "path": row.path,
                    "title": row.title,
                }
            )
    return out


def wiki_doe_hint_lines(factor_names: list[str]) -> list[str]:
    """Advisory notes for build_doe / factor_suggest."""
    bounds = wiki_parameter_bounds()
    if not bounds:
        return []
    lowered = {n.lower(): n for n in factor_names}
    notes: list[str] = []
    for b in bounds:
        pname = b["name"]
        match = next(
            (
                orig
                for low, orig in lowered.items()
                if low == pname.lower() or pname.lower() in low or low in pname.lower()
            ),
            None,
        )
        if match is None:
            continue
        lo = b["min"] if b["min"] is not None else "?"
        hi = b["max"] if b["max"] is not None else "?"
        notes.append(
            f"Wiki 约束 ({b['path']}): {match} / {pname} [{lo}–{hi} {b.get('unit') or ''}]".strip()
        )
    for f in wiki_forbidden()[:5]:
        notes.append(f"Wiki 禁区 ({f['path']}): {f['text']}")
    return notes[:12]
