"""Materials / formulation KG projection for Hub canvas (P2).

SQLite ``kb_entity_links`` → nodes/edges. Not Wiki page [[wikilink]] graph;
Neo4j optional and not required.
"""
from __future__ import annotations

import time
from typing import Any

from ...db.entity_store import SEMANTIC_LINK_TYPES, get_entity_store
from .retrieval import kg_enabled


def expand_relation_type_filters(raw: list[str] | None) -> list[str]:
    """Expand comma filters; support prefix ``measured_*`` → known measured_* types."""
    if not raw:
        return ["substitutes", "measured_performance"]
    known = sorted(SEMANTIC_LINK_TYPES)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        t = (item or "").strip().lower()
        if not t:
            continue
        if t.endswith("_*") or t.endswith("*"):
            prefix = t.rstrip("*").rstrip("_")
            for k in known:
                if k.startswith(prefix) and k not in seen:
                    seen.add(k)
                    out.append(k)
            continue
        if t in seen:
            continue
        # Allow exact semantic types; ignore unknown names silently
        if t in SEMANTIC_LINK_TYPES or t in known:
            seen.add(t)
            out.append(t)
        elif t.startswith("measured_"):
            # forward-compatible measured_* even if not yet in SEMANTIC set
            seen.add(t)
            out.append(t)
    return out or ["substitutes", "measured_performance"]


def _display_name(ent) -> str:
    if ent is None:
        return ""
    name = (ent.canonical_name or ent.id or "").strip()
    if ent.zh_name:
        return f"{ent.zh_name} ({name})" if name else str(ent.zh_name)
    return name or ent.id


def build_material_graph(
    *,
    relation_types: list[str] | None = None,
    limit: int = 500,
    scan_links: int = 8000,
) -> dict[str, Any]:
    """Return {ok, nodes, edges, meta} for Hub materials KG canvas."""
    if not kg_enabled():
        raise PermissionError("kg_enabled is false")

    t0 = time.perf_counter()
    types = expand_relation_type_filters(relation_types)
    store = get_entity_store()
    links = store.list_semantic_links(link_types=types, limit=scan_links, valid_only=True)

    out_degree: dict[str, int] = {}
    in_degree: dict[str, int] = {}
    edge_rows: list[tuple[str, str, str, float, str]] = []  # src,dst,type,conf,method

    for link in links:
        src = link.src_entity_id
        dst = link.dst_entity_id
        if not src or not dst or src == dst:
            continue
        lt = link.link_type or ""
        conf = float(link.confidence or 0.5)
        method = (link.extraction_method or "rule").strip() or "rule"
        key_seen = (src, dst, lt)
        # dedupe identical typed edges (keep first = highest confidence due to order)
        if any(e[0] == src and e[1] == dst and e[2] == lt for e in edge_rows):
            continue
        edge_rows.append((src, dst, lt, conf, method))
        out_degree[src] = out_degree.get(src, 0) + 1
        in_degree[dst] = in_degree.get(dst, 0) + 1
        in_degree.setdefault(src, 0)
        out_degree.setdefault(dst, 0)

    def deg(eid: str) -> int:
        return int(out_degree.get(eid, 0)) + int(in_degree.get(eid, 0))

    entity_ids = sorted(set(out_degree) | set(in_degree), key=lambda e: (-deg(e), e))
    truncated = len(entity_ids) > limit
    kept_ids = entity_ids[: max(1, min(limit, len(entity_ids)))] if entity_ids else []
    kept = set(kept_ids)

    ents = store.get_entities_by_ids(kept_ids)
    nodes = [
        {
            "id": eid,
            "label": _display_name(ents.get(eid)) or eid,
            "kind": (ents[eid].kind if eid in ents else "entity") or "entity",
            "degree": deg(eid),
            "degree_in": int(in_degree.get(eid, 0)),
            "degree_out": int(out_degree.get(eid, 0)),
        }
        for eid in kept_ids
    ]

    edges = [
        {
            "source": src,
            "target": dst,
            "weight": conf,
            "relation_type": lt,
            "extraction_method": method,
        }
        for src, dst, lt, conf, method in edge_rows
        if src in kept and dst in kept
    ]

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "ok": True,
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "truncated": truncated,
            "elapsed_ms": elapsed_ms,
            "relation_types": types,
            "scanned_links": len(links),
            "backend": "sqlite",
        },
    }
