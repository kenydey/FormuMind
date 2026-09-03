"""KG retrieval fusion kernel — shared by Chat and CRAG."""
from __future__ import annotations

import logging
import re
from collections import defaultdict

from ...config import Settings, get_settings
from ...domain.kg_schemas import (
    EntityResolutionSummary,
    KGRetrieveResponse,
    KGRetrieveStats,
    RetrievalPlan,
)
from ...domain.schemas import Evidence, EvidenceEntityRef
from .. import kb_index
from ..errors import degrade_return
from .entity_resolver import resolve_query
from .prompts import TRADE_PRODUCT_DISCLAIMER

logger = logging.getLogger(__name__)

_IMPLEMENT_RE = re.compile(r"实施例|embodiment|example|formulation", re.IGNORECASE)


def kg_enabled() -> bool:
    return get_settings().kg_enabled


def retrieve(
    query: str,
    *,
    mode: str = "auto",
    project_id: str | None = None,
    scan_limit: int | None = None,
    chunk_cap: int | None = None,
    llm_cap: int | None = None,
    max_sources: int | None = None,
    k_semantic: int | None = None,
    pre_evidence: list[Evidence] | None = None,
    settings: Settings | None = None,
) -> KGRetrieveResponse:
    settings = settings or get_settings()
    if not settings.kg_enabled:
        hits = kb_index.search_chunks(query, k=k_semantic or settings.kb_chat_top_k, project_id=project_id)
        pre = list(pre_evidence or [])
        seen = {e.identifier for e in pre}
        merged = pre + [h for h in hits if h.identifier not in seen]
        return KGRetrieveResponse(
            plan=RetrievalPlan(mode="semantic", entity_ids=[]),
            evidence=merged[: k_semantic or settings.kb_chat_top_k],
            stats=KGRetrieveStats(semantic_hits=len(hits)),
        )

    resolved = resolve_query(query, settings=settings)
    plan_mode = mode if mode != "auto" else resolved.mode
    if plan_mode == "auto":
        plan_mode = "semantic"

    scan_limit = scan_limit if scan_limit is not None else settings.kg_enumerative_scan_limit
    chunk_cap = chunk_cap if chunk_cap is not None else settings.kg_enumerative_chunk_cap
    llm_cap = llm_cap if llm_cap is not None else settings.kg_enumerative_llm_cap
    max_sources = max_sources if max_sources is not None else settings.kg_enumerative_max_sources

    plan = RetrievalPlan(
        mode=plan_mode,
        entity_ids=resolved.expanded_entity_ids,
        trade_only=resolved.trade_only,
    )

    mention_evs: list[tuple[float, Evidence, str]] = []
    scan_total = 0
    truncated = False

    if plan_mode in ("enumerative", "hybrid") and resolved.expanded_entity_ids:
        mention_evs, scan_total, truncated = _mention_evidence(
            resolved.expanded_entity_ids,
            project_id=project_id,
            scan_limit=scan_limit,
            chunk_cap=chunk_cap,
            trade_only=resolved.trade_only,
        )

    sem_k = k_semantic or (
        settings.kg_hybrid_semantic_k if plan_mode == "hybrid" else settings.kb_chat_top_k
    )
    semantic = kb_index.search_chunks(query, k=sem_k, project_id=project_id)

    pool: list[tuple[float, Evidence, str]] = list(mention_evs)
    for ev in semantic:
        pool.append((float(ev.relevance), ev, _chunk_id_from_identifier(ev.identifier)))

    if pre_evidence:
        for ev in pre_evidence:
            pool.append((1.0, ev, _chunk_id_from_identifier(ev.identifier)))

    if plan_mode == "hybrid":
        pool.sort(key=lambda x: x[0], reverse=True)
        enum_part = [p for p in pool if p[0] >= 0.5][: settings.kg_hybrid_enumerative_k]
        sem_part = [p for p in pool if p not in enum_part][: settings.kg_hybrid_semantic_k]
        pool = enum_part + sem_part

    capped, dedupe_count, sent_trunc = _apply_caps(
        pool,
        llm_cap=llm_cap,
        max_sources=max_sources,
        trade_only=resolved.trade_only,
    )

    truncated = truncated or sent_trunc or dedupe_count > chunk_cap

    if settings.kg_chat_entity_refs_on_evidence:
        evidence = [_attach_entity_refs(ev, cid) for ev, cid in capped]
    else:
        evidence = [ev for ev, _ in capped]

    stats = KGRetrieveStats(
        scan_total=scan_total,
        chunks_after_dedupe=dedupe_count,
        chunks_sent_to_llm=len(evidence),
        mention_hits=len(mention_evs),
        semantic_hits=len(semantic),
        truncated=truncated,
        trade_only=resolved.trade_only,
    )

    return KGRetrieveResponse(plan=plan, evidence=evidence, stats=stats)


def build_resolution_summary(query: str, resolved=None) -> EntityResolutionSummary:
    if resolved is None:
        resolved = resolve_query(query)
    return EntityResolutionSummary(
        query=query,
        chemicals=resolved.chemicals,
        trade_products=resolved.trade_products,
        top_relations=resolved.top_relations,
        mode=resolved.mode,
        truncated=False,
    )


def trade_product_prompt_suffix(evidence: list[Evidence]) -> str:
    for ev in evidence:
        for ref in ev.entity_refs or []:
            if ref.composition_status in ("unknown", "proprietary", "mixture"):
                return "\n\n" + TRADE_PRODUCT_DISCLAIMER
    return ""


def _mention_evidence(
    entity_ids: list[str],
    *,
    project_id: str | None,
    scan_limit: int,
    chunk_cap: int,
    trade_only: bool,
) -> tuple[list[tuple[float, Evidence, str]], int, bool]:
    from ...db.chunk_store import get_chunk_store
    from ...db.entity_store import get_entity_store
    from ...db.models import SourceDocument

    store = get_entity_store()
    mentions, scan_total, truncated = store.fetch_mentions_for_entities(
        entity_ids, scan_limit=scan_limit, project_id=project_id
    )
    chunk_ids: list[str] = []
    seen_chunk: set[str] = set()
    for m in mentions:
        if m.chunk_id in seen_chunk:
            continue
        seen_chunk.add(m.chunk_id)
        chunk_ids.append(m.chunk_id)
        if len(chunk_ids) >= chunk_cap:
            break

    chunks = {c.id: c for c in get_chunk_store().all_chunks(limit=None) if c.id in chunk_ids}
    source_titles: dict[str, tuple[str, str]] = {}
    try:
        from ...db.source_store import get_source_store

        with get_source_store()._session_factory() as session:
            for sid in {m.source_id for m in mentions}:
                doc = session.get(SourceDocument, sid)
                if doc:
                    source_titles[sid] = (doc.title or doc.filename, doc.source_kind or "local")
    except Exception as exc:
        degrade_return(logger, exc, "mention source titles failed", None)

    mention_by_chunk: dict[str, list] = defaultdict(list)
    for m in mentions:
        mention_by_chunk[m.chunk_id].append(m)

    out: list[tuple[float, Evidence, str]] = []
    for cid in chunk_ids:
        chunk = chunks.get(cid)
        if not chunk:
            continue
        title, kind = source_titles.get(chunk.source_id, ("KB chunk", "local"))
        score = 0.7
        ms = mention_by_chunk.get(cid) or []
        if trade_only and any(m.surface_form for m in ms):
            score += 1.0
        if _IMPLEMENT_RE.search(chunk.text or ""):
            score += 0.5
        ev = Evidence(
            source=kind,
            identifier=f"kb:{cid}",
            title=title,
            snippet=(chunk.text or "")[:800],
            relevance=min(score / 2.5, 1.0),
        )
        out.append((score, ev, cid))
    out.sort(key=lambda x: x[0], reverse=True)
    return out, scan_total, truncated


def _apply_caps(
    pool: list[tuple[float, Evidence, str]],
    *,
    llm_cap: int,
    max_sources: int,
    trade_only: bool,
) -> tuple[list[tuple[Evidence, str]], int, bool]:
    seen_id: set[str] = set()
    deduped: list[tuple[float, Evidence, str]] = []
    for score, ev, cid in sorted(pool, key=lambda x: x[0], reverse=True):
        key = ev.identifier or cid
        if key in seen_id:
            continue
        seen_id.add(key)
        deduped.append((score, ev, cid))

    by_source: dict[str, list[tuple[float, Evidence, str]]] = defaultdict(list)
    for item in deduped:
        sid = item[1].source or "local"
        by_source[sid].append(item)

    final: list[tuple[Evidence, str]] = []
    sources_used: set[str] = set()
    truncated = len(deduped) > llm_cap

    while len(final) < llm_cap and by_source:
        progressed = False
        for sid in sorted(by_source.keys(), key=lambda s: max(x[0] for x in by_source[s]), reverse=True):
            if len(final) >= llm_cap:
                break
            if sid not in sources_used and len(sources_used) >= max_sources:
                continue
            bucket = by_source.get(sid) or []
            if not bucket:
                continue
            score, ev, cid = bucket.pop(0)
            final.append((ev, cid))
            sources_used.add(sid)
            progressed = True
            if not bucket:
                del by_source[sid]
        if not progressed:
            break

    return final, len(deduped), truncated


def _chunk_id_from_identifier(identifier: str) -> str:
    if identifier.startswith("kb:"):
        return identifier[3:]
    return ""


def _attach_entity_refs(ev: Evidence, chunk_id: str) -> Evidence:
    if not chunk_id:
        return ev
    from ...db.entity_store import get_entity_store
    from ...db.models import KGMention, KGEntity

    store = get_entity_store()
    refs: list[EvidenceEntityRef] = []
    try:
        with store._session_factory() as session:
            rows = (
                session.query(KGMention, KGEntity)
                .join(KGEntity, KGMention.entity_id == KGEntity.id)
                .filter(KGMention.chunk_id == chunk_id)
                .limit(12)
                .all()
            )
            for mention, ent in rows:
                refs.append(
                    EvidenceEntityRef(
                        entity_id=ent.id,
                        kind=ent.kind,
                        display_name=ent.canonical_name,
                        composition_status=ent.composition_status or "unknown",
                        surface_form=mention.surface_form or None,
                    )
                )
    except Exception as exc:
        degrade_return(logger, exc, "attach entity_refs failed", None)
    if not refs:
        return ev
    return ev.model_copy(update={"entity_refs": refs})
