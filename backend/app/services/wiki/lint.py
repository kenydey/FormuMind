"""Lightweight wiki lint (W4/S1/S5) — stale / conflict / orphan / broken + actionable chips."""
from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .schema import parse_front_matter

logger = logging.getLogger(__name__)

_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_ORPHAN_SKIP_KINDS = frozenset({"report", "query"})
_ORPHAN_SKIP_PREFIXES = ("themes/project-", "reports/", "queries/")
_LINT_MANAGED_FLAGS = frozenset({"stale", "conflict", "orphan", "missing", "broken"})
_FUZZY_MIN_SCORE = 0.48


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


def _link_targets_from_markdown(md: str) -> set[str]:
    targets: set[str] = set()
    for m in _WIKILINK.finditer(md or ""):
        raw = (m.group(1) or "").strip().lower()
        if not raw:
            continue
        targets.add(raw)
        if ":" in raw:
            targets.add(raw.split(":", 1)[-1].strip())
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


def build_alias_to_path(*, limit: int = 500) -> dict[str, str]:
    store = get_wiki_store()
    rows = store.list_pages(limit=min(500, max(50, limit)))
    alias_to_path: dict[str, str] = {}
    for r in rows:
        for a in _page_aliases(r):
            alias_to_path.setdefault(a, r.path)
    return alias_to_path


def unresolved_wikilinks(md: str, alias_to_path: dict[str, str]) -> list[str]:
    """Return unresolved [[targets]] (raw, de-duped, capped)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _WIKILINK.finditer(md or ""):
        raw = (m.group(1) or "").strip()
        if not raw:
            continue
        key = raw.lower()
        if key in seen:
            continue
        # Resolve using same normalize as orphan scan
        targets = {key}
        if ":" in key:
            targets.add(key.split(":", 1)[-1].strip())
        if "/" in key:
            targets.add(key.rsplit("/", 1)[-1].replace(".md", ""))
        if any(alias_to_path.get(t) for t in targets):
            continue
        seen.add(key)
        out.append(raw)
        if len(out) >= 8:
            break
    return out


def _normalize_link_token(raw: str) -> str:
    key = (raw or "").strip().lower()
    if ":" in key:
        key = key.split(":", 1)[-1].strip()
    if "/" in key:
        key = key.rsplit("/", 1)[-1].replace(".md", "")
    return key


def _wikilink_for_row(row) -> str:
    kind = (row.kind or "page").strip() or "page"
    nk = (row.norm_key or "").strip()
    title = (row.title or "").strip() or nk or (row.path or "page")
    if nk:
        return f"[[{kind}:{nk}|{title}]]"
    return f"[[{title}]]"


def suggest_broken_fixes(
    broken: str,
    *,
    exclude_path: str = "",
    limit: int = 3,
    min_score: float = _FUZZY_MIN_SCORE,
) -> list[dict[str, Any]]:
    """Fuzzy-match an unresolved [[target]] against wiki titles / keys (S5)."""
    token = _normalize_link_token(broken)
    if not token or len(token) < 2:
        return []
    store = get_wiki_store()
    rows = store.list_pages(limit=400)
    scored: list[tuple[float, str, str, str]] = []
    excl = (exclude_path or "").replace("\\", "/")
    for r in rows:
        p = (r.path or "").replace("\\", "/")
        if not p or p == excl:
            continue
        kind = (r.kind or "").lower()
        if kind in {"report", "query"}:
            continue
        if p.startswith("reports/") or p.startswith("queries/"):
            continue
        best = 0.0
        for alias in _page_aliases(r):
            ratio = SequenceMatcher(None, token, alias).ratio()
            if token in alias or alias in token:
                ratio = max(ratio, 0.72)
            common = 0
            for a, b in zip(token, alias):
                if a != b:
                    break
                common += 1
            if common >= 3:
                ratio = max(ratio, min(0.9, 0.5 + 0.05 * common))
            if ratio > best:
                best = ratio
        if best < min_score:
            continue
        scored.append((best, p, (r.title or "").strip() or p, _wikilink_for_row(r)))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for score, p, label, wikilink in scored:
        if p in seen_paths:
            continue
        seen_paths.add(p)
        out.append(
            {
                "broken": broken,
                "path": p,
                "label": label,
                "wikilink": wikilink,
                "score": round(float(score), 3),
            }
        )
        if len(out) >= max(1, limit):
            break
    return out


def apply_broken_fix(
    *,
    path: str,
    broken: str,
    replacement_path: str,
    mode: str = "rewrite",
) -> dict[str, Any]:
    """Rewrite or append a related [[wikilink]] for a broken target (explicit user action).

    Does not touch L1 numerical tables / Claims — only wiki markdown links.
    """
    settings = get_settings()
    if not settings.wiki_enabled:
        raise PermissionError("wiki_enabled is false")
    store = get_wiki_store()
    row = store.get_by_path(path)
    if row is None:
        raise LookupError(f"wiki page not found: {path}")
    target = store.get_by_path(replacement_path)
    if target is None:
        raise LookupError(f"replacement page not found: {replacement_path}")

    md = store.read_markdown(path) or ""
    wikilink = _wikilink_for_row(target)
    broken_tok = (broken or "").strip()
    if not broken_tok:
        raise ValueError("broken target required")

    want = (mode or "rewrite").strip().lower()
    if want not in {"rewrite", "append_related"}:
        want = "rewrite"

    applied_mode = "noop"
    new_md = md

    if want == "rewrite":
        pattern = re.compile(
            r"\[\[" + re.escape(broken_tok) + r"(?:[|#][^\]]*)?\]\]",
            re.IGNORECASE,
        )
        new_md, n = pattern.subn(wikilink, md, count=1)
        if n > 0:
            applied_mode = "rewrite"

    if applied_mode == "noop":
        line = f"- {wikilink}  <!-- fixed from [[{broken_tok}]] -->"
        if re.search(r"(?im)^##\s+Related\b", new_md):

            def _inject(m: re.Match[str]) -> str:
                return m.group(0) + line + "\n"

            new_md2, n = re.subn(
                r"(?im)^##\s+Related[^\n]*\n",
                _inject,
                new_md,
                count=1,
            )
            new_md = new_md2 if n else (new_md.rstrip() + f"\n\n## Related\n{line}\n")
        else:
            new_md = new_md.rstrip() + f"\n\n## Related\n{line}\n"
        applied_mode = "append_related"

    store.upsert_page(
        path=row.path,
        kind=row.kind,
        title=row.title or "",
        norm_key=row.norm_key or "",
        entity_id=row.entity_id,
        markdown=new_md,
        source_ids=list(row.source_ids or []),
        flags=list(row.flags or []),
    )
    flags = lint_and_persist(path)
    return {
        "ok": True,
        "path": path,
        "broken": broken_tok,
        "replacement_path": replacement_path,
        "wikilink": wikilink,
        "mode": applied_mode,
        "flags": flags,
    }


def suggest_link_candidates(
    *,
    path: str,
    source_ids: list[str] | None = None,
    limit: int = 3,
) -> list[dict[str, str]]:
    """Heuristic themes/dossiers that could link to an orphan page (read-only)."""
    store = get_wiki_store()
    rows = store.list_pages(limit=300)
    page_sources = {str(s) for s in (source_ids or []) if s}
    scored: list[tuple[int, str, str]] = []
    for r in rows:
        p = (r.path or "").replace("\\", "/")
        if not p or p == path:
            continue
        kind = (r.kind or "").lower()
        score = 0
        if p.startswith("themes/project-"):
            score += 50
        elif kind == "theme":
            score += 20
        elif kind == "system":
            score += 10
        else:
            continue
        rs = {str(s) for s in (r.source_ids or []) if s}
        if page_sources and (page_sources & rs):
            score += 30
        if score <= 0:
            continue
        scored.append((score, p, (r.title or "").strip() or p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"path": p, "label": label} for _, p, label in scored[: max(1, limit)]]


def suggest_actions(
    *,
    flags: list[str],
    kind: str,
    path: str,
    title: str = "",
    norm_key: str = "",
    source_ids: list[str] | None = None,
    broken_targets: list[str] | None = None,
    broken_fixes: list[dict[str, Any]] | None = None,
    link_candidates: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Hub-facing action chips for a flagged page (no side effects / no L1 writes)."""
    fl = {str(f) for f in (flags or [])}
    actions: list[dict[str, str]] = [
        {"id": "open_page", "label": "打开页面", "hint": path, "target": path},
    ]
    if "unreviewed" in fl:
        actions.append(
            {
                "id": "mark_reviewed",
                "label": "标记已审",
                "hint": "POST /api/wiki/pages/review",
                "target": path,
            }
        )
    if "stale" in fl or "missing" in fl:
        n_src = len(source_ids or [])
        actions.append(
            {
                "id": "check_sources",
                "label": "核对 Evidence",
                "hint": f"source_ids={n_src} · 补文献或重新 compile",
                "target": path,
            }
        )
    if "conflict" in fl:
        actions.append(
            {
                "id": "fix_bounds",
                "label": "核对 bounds",
                "hint": "同名上下限倒置或冲突 — 打开页核对 bounds_json",
                "target": path,
            }
        )
    if "broken" in fl or broken_targets:
        sample = ", ".join((broken_targets or [])[:3]) or "未解析 [[wikilink]]"
        actions.append(
            {
                "id": "fix_broken",
                "label": "查看断链",
                "hint": f"断链：{sample}",
                "target": path,
            }
        )
        fixes = broken_fixes
        if fixes is None and broken_targets:
            fixes = []
            for bt in (broken_targets or [])[:3]:
                fixes.extend(suggest_broken_fixes(bt, exclude_path=path, limit=2))
        seen_fix_paths: set[str] = set()
        fix_i = 0
        for fx in fixes or []:
            rp = str(fx.get("path") or "").strip()
            if not rp or rp in seen_fix_paths:
                continue
            seen_fix_paths.add(rp)
            fix_i += 1
            br = str(fx.get("broken") or (broken_targets or [""])[0] or "")
            label = str(fx.get("label") or rp)
            score = fx.get("score")
            score_s = f" · score={score}" if score is not None else ""
            actions.append(
                {
                    "id": f"apply_broken_fix_{fix_i}",
                    "label": f"改链→ {label}",
                    "hint": f"[[{br}]] → {fx.get('wikilink') or rp}{score_s}（显式点击才写）",
                    "target": path,
                    "broken": br,
                    "replacement_path": rp,
                    "mode": "rewrite",
                }
            )
            if fix_i >= 3:
                break
    if "orphan" in fl:
        cands = link_candidates
        if cands is None:
            cands = suggest_link_candidates(path=path, source_ids=source_ids, limit=3)
        if cands:
            first = cands[0]
            title_hint = (title or path).strip()
            actions.append(
                {
                    "id": "link_from_theme",
                    "label": f"打开补链候选：{first.get('label') or first.get('path')}",
                    "hint": f"在候选页手动增加 [[{title_hint}]]（不自动改 L1）",
                    "target": first.get("path") or "",
                }
            )
            for i, c in enumerate(cands[1:], start=2):
                actions.append(
                    {
                        "id": f"open_link_candidate_{i}",
                        "label": f"候选 {i}：{c.get('label') or c.get('path')}",
                        "hint": c.get("path") or "",
                        "target": c.get("path") or "",
                    }
                )
        else:
            actions.append(
                {
                    "id": "link_from_theme",
                    "label": "从综述/卷宗补链",
                    "hint": "暂无候选 — 打开本页后从卷宗手动补 [[wikilink]]",
                    "target": path,
                }
            )
    if (kind or "").lower() == "system":
        actions.append(
            {
                "id": "compile_theme",
                "label": "编译体系主题",
                "hint": norm_key or "POST /api/wiki/themes/compile",
                "target": path,
            }
        )
    if path.startswith("themes/project-"):
        actions.append(
            {
                "id": "refresh_dossier",
                "label": "刷新卷宗",
                "hint": "POST /api/wiki/dossier/refresh",
                "target": path,
            }
        )
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for a in actions:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        out.append(a)
    return out


def lint_page(path: str, *, alias_to_path: dict[str, str] | None = None) -> list[str]:
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
    for b in bounds:
        if not isinstance(b, dict):
            continue
        lo = b.get("min", b.get("min_value"))
        hi = b.get("max", b.get("max_value"))
        try:
            lo_f = float(lo) if lo is not None else None
            hi_f = float(hi) if hi is not None else None
        except (TypeError, ValueError):
            continue
        if lo_f is not None and hi_f is not None and lo_f > hi_f:
            flags.append("conflict")
    aliases = alias_to_path if alias_to_path is not None else build_alias_to_path()
    if unresolved_wikilinks(md, aliases):
        flags.append("broken")
    return list(dict.fromkeys(flags))


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
        orphans.add(path)
    return orphans


def lint_and_persist(
    path: str,
    *,
    orphan_paths: set[str] | None = None,
    alias_to_path: dict[str, str] | None = None,
) -> list[str]:
    """Run lint and merge flags into page + disk front-matter."""
    settings = get_settings()
    if not settings.wiki_enabled:
        return []
    store = get_wiki_store()
    row = store.get_by_path(path)
    if row is None:
        return []
    aliases = alias_to_path if alias_to_path is not None else build_alias_to_path()
    found = lint_page(path, alias_to_path=aliases)
    if orphan_paths is not None and path in orphan_paths:
        found.append("orphan")
    keep = [f for f in (row.flags or []) if f not in _LINT_MANAGED_FLAGS]
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
    # No front-matter — still persist flags on row
    store.upsert_page(
        path=row.path,
        kind=row.kind,
        title=row.title or "",
        norm_key=row.norm_key or "",
        entity_id=row.entity_id,
        markdown=md,
        source_ids=list(row.source_ids or []),
        flags=merged,
    )
    return merged


def lint_paths(paths: list[str], *, detect_orphan: bool = False) -> dict[str, list[str]]:
    orphans = detect_orphans() if detect_orphan else None
    aliases = build_alias_to_path()
    out: dict[str, list[str]] = {}
    for p in paths:
        try:
            out[p] = lint_and_persist(p, orphan_paths=orphans, alias_to_path=aliases)
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
    broken_n = sum(1 for fl in by_path.values() if "broken" in fl)
    return {
        "ok": True,
        "scanned": len(paths),
        "flagged": flagged,
        "orphan_count": len(orphans),
        "broken_count": broken_n,
        "results": by_path,
    }


def sweep_flagged_pages(*, limit: int = 200, detect_orphan: bool = True) -> dict[str, Any]:
    """Re-lint currently flagged pages so obsolete lint flags are cleared (S1)."""
    store = get_wiki_store()
    rows = store.list_pages(limit=min(500, max(1, limit * 3)))
    flagged_paths = [r.path for r in rows if r.path and (r.flags or [])]
    flagged_paths = flagged_paths[: max(1, limit)]
    orphans = detect_orphans(limit=limit) if detect_orphan else set()
    aliases = build_alias_to_path(limit=limit)
    cleared = 0
    still = 0
    results: dict[str, list[str]] = {}
    for p in flagged_paths:
        before = list(store.get_by_path(p).flags or []) if store.get_by_path(p) else []
        after = lint_and_persist(p, orphan_paths=orphans, alias_to_path=aliases)
        results[p] = after
        managed_before = [f for f in before if f in _LINT_MANAGED_FLAGS]
        managed_after = [f for f in after if f in _LINT_MANAGED_FLAGS]
        if managed_before and not managed_after:
            cleared += 1
        if managed_after:
            still += 1
    return {
        "ok": True,
        "scanned": len(flagged_paths),
        "cleared": cleared,
        "still_flagged": still,
        "orphan_count": len(orphans),
        "results": results,
    }


def list_flagged_pages(*, limit: int = 100) -> list[dict[str, Any]]:
    store = get_wiki_store()
    rows = store.list_pages(limit=min(500, max(1, limit * 3)))
    aliases = build_alias_to_path()
    flagged = []
    for row in rows:
        fl = list(row.flags or [])
        if not fl:
            continue
        md = store.read_markdown(row.path) or ""
        broken = unresolved_wikilinks(md, aliases) if ("broken" in fl) else []
        broken_fixes: list[dict[str, Any]] = []
        if broken:
            seen_rp: set[str] = set()
            for bt in broken[:3]:
                for fx in suggest_broken_fixes(bt, exclude_path=row.path or "", limit=2):
                    rp = str(fx.get("path") or "")
                    if not rp or rp in seen_rp:
                        continue
                    seen_rp.add(rp)
                    broken_fixes.append(fx)
                    if len(broken_fixes) >= 3:
                        break
                if len(broken_fixes) >= 3:
                    break
        cands = (
            suggest_link_candidates(path=row.path or "", source_ids=list(row.source_ids or []))
            if "orphan" in fl
            else None
        )
        item = {
            "id": row.id,
            "path": row.path,
            "kind": row.kind,
            "title": row.title or "",
            "norm_key": row.norm_key or "",
            "flags": fl,
            "source_ids": list(row.source_ids or []),
            "actions": suggest_actions(
                flags=fl,
                kind=row.kind or "",
                path=row.path or "",
                title=row.title or "",
                norm_key=row.norm_key or "",
                source_ids=list(row.source_ids or []),
                broken_targets=broken,
                broken_fixes=broken_fixes,
                link_candidates=cands,
            ),
        }
        flagged.append(item)
        if len(flagged) >= limit:
            break
    return flagged
