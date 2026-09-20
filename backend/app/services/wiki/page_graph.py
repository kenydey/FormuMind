"""Wiki page link graph — build nodes/edges from [[wikilink]] (P0).

Not the materials/formulation KG. Read-only navigation aid for Hub.
"""
from __future__ import annotations

import time
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .lint import _link_targets_from_markdown, _page_aliases


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


def build_page_graph(
    *,
    limit: int = 500,
    kinds: list[str] | None = None,
    include_orphan: bool = True,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Return {ok, nodes, edges, meta} for Hub Wiki link graph."""
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

    # Collect directed edges (path → path) and broken link count
    edge_set: set[tuple[str, str]] = set()
    broken = 0
    out_degree: dict[str, int] = {r.path: 0 for r in rows}
    in_degree: dict[str, int] = {r.path: 0 for r in rows}

    for r in rows:
        md = store.read_markdown(r.path) or ""
        for t in _link_targets_from_markdown(md):
            target_path = _resolve_target(t, alias_to_path)
            if not target_path:
                broken += 1
                continue
            if target_path == r.path:
                continue
            if target_path not in out_degree:
                # Target filtered out by kind/project — count as broken-ish skip
                broken += 1
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
        },
    }
