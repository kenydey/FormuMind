"""KG-R2 — graph traversal over semantic kb_entity_links."""
from __future__ import annotations

from collections import deque

from ...db.entity_store import SEMANTIC_LINK_TYPES, get_entity_store
from ...db.models import KGEntityLink
from ...domain.kg_schemas import (
    KGPathResponse,
    KGPathStep,
    KGRelationView,
    KGSubstituteCandidate,
    KGSubstituteDiscoverResponse,
    RelationEvidence,
    RelationType,
)


def _entity_display_name(entity_id: str, cache: dict[str, str]) -> str:
    if entity_id in cache:
        return cache[entity_id]
    row = get_entity_store().get_entity(entity_id)
    name = (row.canonical_name if row else entity_id) or entity_id
    if row and row.zh_name:
        name = f"{row.zh_name} ({name})"
    cache[entity_id] = name
    return name


def link_to_view(link: KGEntityLink, *, name_cache: dict[str, str] | None = None) -> KGRelationView:
    cache = name_cache or {}
    evidence = [
        RelationEvidence(
            source_id=ref.get("source_id", ""),
            chunk_id=ref.get("chunk_id"),
            sentence=ref.get("sentence", ""),
            confidence=float(ref.get("confidence", link.confidence or 0.5)),
            extraction_method=ref.get("extraction_method", link.extraction_method or "rule"),
        )
        for ref in (link.evidence_refs or [])
    ]
    rel_type = link.link_type
    try:
        relation_type = RelationType(rel_type)
    except ValueError:
        relation_type = RelationType.SUBSTITUTES
    return KGRelationView(
        id=link.id,
        source_entity_id=link.src_entity_id,
        target_entity_id=link.dst_entity_id,
        relation_type=relation_type,
        confidence=float(link.confidence or 0.5),
        evidence=evidence,
        metadata=dict(link.metadata_json or {}),
        is_valid=bool(link.is_valid),
        extraction_method=link.extraction_method or "rule",
    )


def get_entity_relations(
    entity_id: str,
    *,
    direction: str = "both",
    link_types: list[str] | None = None,
    limit: int = 50,
) -> list[KGRelationView]:
    store = get_entity_store()
    types = link_types or list(SEMANTIC_LINK_TYPES)
    links = store.get_links_for_entity(
        entity_id,
        direction=direction,
        link_types=types,
        limit=limit,
    )
    cache: dict[str, str] = {}
    return [link_to_view(link, name_cache=cache) for link in links]


def find_path(
    src_entity_id: str,
    dst_entity_id: str,
    *,
    max_depth: int = 4,
    link_types: list[str] | None = None,
) -> KGPathResponse:
    if src_entity_id == dst_entity_id:
        cache: dict[str, str] = {}
        return KGPathResponse(
            src_entity_id=src_entity_id,
            dst_entity_id=dst_entity_id,
            found=True,
            hops=0,
            steps=[],
        )

    types = set(link_types or SEMANTIC_LINK_TYPES)
    store = get_entity_store()
    cache: dict[str, str] = {}

    # BFS: state = (current_entity_id, path_of_steps)
    queue: deque[tuple[str, list[KGPathStep]]] = deque([(src_entity_id, [])])
    visited: set[str] = {src_entity_id}

    while queue:
        current, path = queue.popleft()
        if len(path) >= max_depth:
            continue
        links = store.get_links_for_entity(current, direction="both", link_types=list(types), limit=80)
        for link in links:
            rel_view = link_to_view(link, name_cache=cache)
            neighbors: list[tuple[str, bool]] = []
            if link.src_entity_id == current and link.dst_entity_id not in visited:
                neighbors.append((link.dst_entity_id, True))
            if link.dst_entity_id == current and link.src_entity_id not in visited:
                neighbors.append((link.src_entity_id, False))

            for neighbor, forward in neighbors:
                if neighbor in visited:
                    continue
                step = KGPathStep(
                    relation=rel_view,
                    entity_id=neighbor,
                    entity_name=_entity_display_name(neighbor, cache),
                )
                new_path = path + [step]
                if neighbor == dst_entity_id:
                    return KGPathResponse(
                        src_entity_id=src_entity_id,
                        dst_entity_id=dst_entity_id,
                        found=True,
                        hops=len(new_path),
                        steps=new_path,
                    )
                visited.add(neighbor)
                queue.append((neighbor, new_path))

    return KGPathResponse(
        src_entity_id=src_entity_id,
        dst_entity_id=dst_entity_id,
        found=False,
        hops=0,
        steps=[],
    )


def discover_substitutes(
    entity_id: str,
    *,
    limit: int = 10,
    max_depth: int = 2,
) -> KGSubstituteDiscoverResponse:
    store = get_entity_store()
    cache: dict[str, str] = {}
    entity_name = _entity_display_name(entity_id, cache)
    candidates: list[KGSubstituteCandidate] = []
    seen: set[str] = {entity_id}

    def _add_candidate(sub_id: str, rel: KGRelationView, path: list[KGPathStep]) -> None:
        if sub_id in seen:
            return
        seen.add(sub_id)
        candidates.append(
            KGSubstituteCandidate(
                entity_id=sub_id,
                entity_name=_entity_display_name(sub_id, cache),
                relation_type=RelationType.SUBSTITUTES,
                confidence=rel.confidence,
                hops=len(path),
                path=path,
            )
        )

    # Direct substitutes: outgoing substitutes + incoming (reverse)
    direct = store.get_links_for_entity(
        entity_id,
        direction="both",
        link_types=["substitutes"],
        limit=limit * 2,
    )
    for link in direct:
        rel = link_to_view(link, name_cache=cache)
        if link.link_type != "substitutes":
            continue
        if link.src_entity_id == entity_id:
            sub_id = link.dst_entity_id
        else:
            sub_id = link.src_entity_id
        step = KGPathStep(
            relation=rel,
            entity_id=sub_id,
            entity_name=_entity_display_name(sub_id, cache),
        )
        _add_candidate(sub_id, rel, [step])
        if len(candidates) >= limit:
            break

    # One-hop via synergizes/requires to reach another substitute edge
    if len(candidates) < limit and max_depth > 1:
        frontier = list(candidates)
        for cand in frontier:
            if len(candidates) >= limit:
                break
            bridge_links = store.get_links_for_entity(
                cand.entity_id,
                direction="both",
                link_types=["substitutes", "synergizes"],
                limit=20,
            )
            for link in bridge_links:
                if link.link_type != "substitutes":
                    continue
                if link.src_entity_id == cand.entity_id:
                    sub_id = link.dst_entity_id
                else:
                    sub_id = link.src_entity_id
                if sub_id in seen:
                    continue
                rel = link_to_view(link, name_cache=cache)
                path = cand.path + [
                    KGPathStep(
                        relation=rel,
                        entity_id=sub_id,
                        entity_name=_entity_display_name(sub_id, cache),
                    )
                ]
                _add_candidate(sub_id, rel, path)
                if len(candidates) >= limit:
                    break

    candidates.sort(key=lambda c: (-c.confidence, c.hops))
    return KGSubstituteDiscoverResponse(
        query_entity_id=entity_id,
        query_entity_name=entity_name,
        substitutes=candidates[:limit],
    )
