"""Wiki page link graph — build nodes/edges from [[wikilink]] (P0/P1).

Not the materials/formulation KG. Read-only navigation aid for Hub.
P1 adds insights: orphans / isolates / broken samples / weak components.
"""
from __future__ import annotations

import time
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .lint import (
    _ORPHAN_SKIP_KINDS,
    _ORPHAN_SKIP_PREFIXES,
    _link_targets_from_markdown,
    _page_aliases,
)

_INSIGHT_CAP = 40
_BROKEN_CAP = 50


def page_graph_enabled() -> bool:
    settings = get_settings()
    return bool(
        settings.wiki_enabled and getattr(settings, "wiki_page_graph_enabled", False)
    )


def _resolve_target(target: str, alias_to_path: dict[str, str]) -> str | None:
    t = (target or "").strip().lower()
    if not t:
        return None
    hit = alias_to_path.get(t)
    if hit:
        return hit
    # kind:key already expanded in _link_targets_from_markdown
    return alias_to_path.get(t.replace(".md", ""))


def _lint_orphan_candidate(path: str, kind: str) -> bool:
    """Align with lint.detect_orphans skip rules (structure pages excluded)."""
    if (kind or "").lower() in _ORPHAN_SKIP_KINDS:
        return False
    if any((path or "").startswith(p) for p in _ORPHAN_SKIP_PREFIXES):
        return False
    return True


def _weak_components(paths: set[str], edges: set[tuple[str, str]]) -> tuple[int, int]:
    """Undirected connected components over page paths. Returns (count, largest_size)."""
    if not paths:
        return 0, 0
    parent: dict[str, str] = {p: p for p in paths}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in edges:
        if a in parent and b in parent:
            union(a, b)

    sizes: dict[str, int] = {}
    for p in paths:
        root = find(p)
        sizes[root] = sizes.get(root, 0) + 1
    if not sizes:
        return 0, 0
    return len(sizes), max(sizes.values())


def build_page_graph(
    *,
    limit: int = 500,
    kinds: list[str] | None = None,
    include_orphan: bool = True,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Return {ok, nodes, edges, meta, insights} for Hub Wiki link graph."""
    if not page_graph_enabled():
        raise PermissionError("wiki_page_graph_enabled is false")

    t0 = time.perf_counter()
    store = get_wiki_store()
    scan_cap = min(2000, max(limit * 4, 200))
    rows = store.list_pages(limit=scan_cap, offset=0)

    if project_id:
        from .project_scope import filter_wiki_rows

        rows = filter_wiki_rows(rows, project_id)

    kind_filter: set[str] | None = None
    if kinds:
        kind_filter = {k.strip().lower() for k in kinds if k and k.strip()}

    if kind_filter:
        rows = [r for r in rows if (r.kind or "").lower() in kind_filter]

    alias_to_path: dict[str, str] = {}
    for r in rows:
        for a in _page_aliases(r):
            alias_to_path.setdefault(a, r.path)

    # Collect directed edges (path → path) and broken link samples
    edge_set: set[tuple[str, str]] = set()
    broken = 0
    broken_items: list[dict[str, Any]] = []
    out_degree: dict[str, int] = {r.path: 0 for r in rows}
    in_degree: dict[str, int] = {r.path: 0 for r in rows}
    title_of = {
        r.path: ((r.title or "").strip() or r.path) for r in rows
    }

    for r in rows:
        md = store.read_markdown(r.path) or ""
        for t in _link_targets_from_markdown(md):
            target_path = _resolve_target(t, alias_to_path)
            if not target_path:
                broken += 1
                if len(broken_items) < _BROKEN_CAP:
                    broken_items.append(
                        {
                            "source": r.path,
                            "source_label": title_of.get(r.path, r.path),
                            "target": t,
                            "reason": "unresolved",
                        }
                    )
                continue
            if target_path == r.path:
                continue
            if target_path not in out_degree:
                broken += 1
                if len(broken_items) < _BROKEN_CAP:
                    broken_items.append(
                        {
                            "source": r.path,
                            "source_label": title_of.get(r.path, r.path),
                            "target": t,
                            "reason": "filtered_out",
                        }
                    )
                continue
            key = (r.path, target_path)
            if key in edge_set:
                continue
            edge_set.add(key)
            out_degree[r.path] = out_degree.get(r.path, 0) + 1
            in_degree[target_path] = in_degree.get(target_path, 0) + 1

    # Degree for ranking / orphan filter
    def deg(path: str) -> int:
        return int(out_degree.get(path, 0)) + int(in_degree.get(path, 0))

    # P1 insights from full scanned set (before limit truncate)
    orphans: list[dict[str, Any]] = []
    isolates: list[dict[str, Any]] = []
    for r in rows:
        path = r.path or ""
        di = int(in_degree.get(path, 0))
        do = int(out_degree.get(path, 0))
        item = {
            "path": path,
            "label": title_of.get(path, path),
            "kind": r.kind or "page",
            "degree_in": di,
            "degree_out": do,
            "degree": di + do,
        }
        if di + do == 0:
            isolates.append(item)
        if di == 0 and _lint_orphan_candidate(path, r.kind or ""):
            orphans.append(item)

    orphans.sort(key=lambda x: (x["path"] or ""))
    isolates.sort(key=lambda x: (x["path"] or ""))
    comp_count, comp_largest = _weak_components({r.path for r in rows}, edge_set)

    candidates = list(rows)
    if not include_orphan:
        candidates = [r for r in candidates if deg(r.path) > 0]

    candidates.sort(key=lambda r: (-deg(r.path), r.path or ""))
    truncated = len(candidates) > limit
    kept = candidates[: max(1, min(limit, len(candidates)))] if candidates else []
    kept_paths = {r.path for r in kept}

    nodes = [
        {
            "id": r.path,
            "path": r.path,
            "label": (r.title or "").strip() or r.path,
            "kind": r.kind or "page",
            "flags": list(r.flags or []),
            "degree": deg(r.path),
            "degree_in": int(in_degree.get(r.path, 0)),
            "degree_out": int(out_degree.get(r.path, 0)),
        }
        for r in kept
    ]

    edges = [
        {"source": a, "target": b, "weight": 1.0}
        for a, b in sorted(edge_set)
        if a in kept_paths and b in kept_paths
    ]

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "ok": True,
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "broken_links": broken,
            "truncated": truncated,
            "scanned_pages": len(rows),
            "elapsed_ms": elapsed_ms,
            "project_id": project_id,
            "include_orphan": include_orphan,
            "orphan_count": len(orphans),
            "isolate_count": len(isolates),
            "component_count": comp_count,
            "largest_component": comp_largest,
        },
        "insights": {
            "orphans": orphans[:_INSIGHT_CAP],
            "isolates": isolates[:_INSIGHT_CAP],
            "broken": broken_items,
            "components": {
                "count": comp_count,
                "largest": comp_largest,
            },
        },
    }
