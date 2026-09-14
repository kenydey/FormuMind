"""Lightweight wiki lint (W4) — stale / conflict / orphan + actionable hints."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .schema import parse_front_matter

logger = logging.getLogger(__name__)

_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_ORPHAN_SKIP_KINDS = frozenset({"report"})
_ORPHAN_SKIP_PREFIXES = ("themes/project-", "reports/")


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


def suggest_actions(*, flags: list[str], kind: str, path: str) -> list[dict[str, str]]:
    """Hub-facing action chips for a flagged page (no side effects)."""
    fl = {str(f) for f in (flags or [])}
    actions: list[dict[str, str]] = [
        {"id": "open_page", "label": "打开页面", "hint": path},
    ]
    if "unreviewed" in fl:
        actions.append(
            {"id": "mark_reviewed", "label": "标记已审", "hint": "POST /api/wiki/pages/review"}
        )
    if "stale" in fl or "missing" in fl:
        actions.append(
            {
                "id": "check_sources",
                "label": "核对 source_ids / Evidence",
                "hint": "补文献或重新 compile",
            }
        )
    if "conflict" in fl:
        actions.append(
            {
                "id": "fix_bounds",
                "label": "核对 bounds_json",
                "hint": "同名上下限倒置或冲突",
            }
        )
    if "orphan" in fl:
        actions.append(
            {
                "id": "link_from_theme",
                "label": "从综述/卷宗补链",
                "hint": "增加 [[wikilink]] 指向本页",
            }
        )
    if (kind or "").lower() == "system":
        actions.append(
            {
                "id": "compile_theme",
                "label": "编译体系主题",
                "hint": "POST /api/wiki/themes/compile",
            }
        )
    if path.startswith("themes/project-"):
        actions.append(
            {
                "id": "refresh_dossier",
                "label": "刷新卷宗",
                "hint": "POST /api/wiki/dossier/refresh",
            }
        )
    # de-dupe by id
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for a in actions:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        out.append(a)
    return out


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


def _link_targets_from_markdown(md: str) -> set[str]:
    targets: set[str] = set()
    for m in _WIKILINK.finditer(md or ""):
        raw = (m.group(1) or "").strip().lower()
        if not raw:
            continue
        targets.add(raw)
        # chemical:foo / material:bar → also keep suffix
        if ":" in raw:
            targets.add(raw.split(":", 1)[-1].strip())
        # path-like
        if "/" in raw:
            targets.add(raw.rsplit("/", 1)[-1].replace(".md", ""))
    return targets


def _page_aliases(row) -> set[str]:
    aliases: set[str] = set()
    path = (row.path or "").replace("\\", "/").lower()
    aliases.add(path)
    aliases.add(path.replace(".md", ""))
    if "/" in path:
        aliases.add(path.rsplit("/", 1)[-1].replace(".md", ""))
    if row.norm_key:
        aliases.add(str(row.norm_key).strip().lower())
    if row.title:
        aliases.add(str(row.title).strip().lower())
    if row.entity_id:
        aliases.add(str(row.entity_id).strip().lower())
        if ":" in str(row.entity_id):
            aliases.add(str(row.entity_id).split(":", 1)[-1].strip().lower())
    return {a for a in aliases if a}


def detect_orphans(*, limit: int = 500) -> set[str]:
    """Paths with no inbound [[wikilink]] from other pages (best-effort)."""
    store = get_wiki_store()
    rows = store.list_pages(limit=min(500, max(50, limit)))
    if len(rows) < 2:
        return set()

    inbound: dict[str, int] = {r.path: 0 for r in rows}
    alias_to_path: dict[str, str] = {}
    for r in rows:
        for a in _page_aliases(r):
            alias_to_path.setdefault(a, r.path)

    for r in rows:
        md = store.read_markdown(r.path) or ""
        for t in _link_targets_from_markdown(md):
            target_path = alias_to_path.get(t)
            if target_path and target_path != r.path:
                inbound[target_path] = inbound.get(target_path, 0) + 1

    orphans: set[str] = set()
    for r in rows:
        path = r.path or ""
        kind = (r.kind or "").lower()
        if kind in _ORPHAN_SKIP_KINDS:
            continue
        if any(path.startswith(p) for p in _ORPHAN_SKIP_PREFIXES):
            continue
        if inbound.get(path, 0) > 0:
            continue
        # Brand-new single pages with sources are still "unlinked" — flag lightly
        orphans.add(path)
    return orphans


def lint_and_persist(path: str, *, orphan_paths: set[str] | None = None) -> list[str]:
    """Run lint and merge flags into page + disk front-matter."""
    settings = get_settings()
    if not settings.wiki_enabled:
        return []
    store = get_wiki_store()
    row = store.get_by_path(path)
    if row is None:
        return []
    found = lint_page(path)
    if orphan_paths is not None and path in orphan_paths:
        found.append("orphan")
    keep = [f for f in (row.flags or []) if f not in {"stale", "conflict", "orphan", "missing"}]
    merged = list(dict.fromkeys([*keep, *found]))
    md = store.read_markdown(path) or ""
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


def lint_paths(paths: list[str], *, detect_orphan: bool = False) -> dict[str, list[str]]:
    orphans = detect_orphans() if detect_orphan else None
    out: dict[str, list[str]] = {}
    for p in paths:
        try:
            out[p] = lint_and_persist(p, orphan_paths=orphans)
        except Exception as exc:  # noqa: BLE001
            logger.debug("wiki lint failed for %s: %s", p, exc)
            out[p] = []
    return out


def run_lint_pass(*, limit: int = 200, detect_orphan: bool = True) -> dict[str, Any]:
    """Lint up to ``limit`` pages; optionally mark orphans. Returns summary."""
    store = get_wiki_store()
    rows = store.list_pages(limit=min(500, max(1, limit)))
    paths = [r.path for r in rows if r.path]
    orphans = detect_orphans(limit=limit) if detect_orphan else set()
    by_path = lint_paths(paths, detect_orphan=detect_orphan)
    flagged = sum(1 for fl in by_path.values() if fl)
    return {
        "ok": True,
        "scanned": len(paths),
        "flagged": flagged,
        "orphan_count": len(orphans),
        "results": by_path,
    }


def list_flagged_pages(*, limit: int = 100) -> list[dict[str, Any]]:
    store = get_wiki_store()
    rows = store.list_pages(limit=min(500, max(1, limit * 3)))
    flagged = []
    for row in rows:
        fl = list(row.flags or [])
        if not fl:
            continue
        item = {
            "id": row.id,
            "path": row.path,
            "kind": row.kind,
            "title": row.title,
            "flags": fl,
            "source_ids": list(row.source_ids or []),
            "actions": suggest_actions(flags=fl, kind=row.kind or "", path=row.path or ""),
        }
        flagged.append(item)
        if len(flagged) >= limit:
            break
    return flagged
