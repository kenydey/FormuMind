"""Link document chunks to normalized KG entities and mentions."""
from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy.orm import Session

from ...config import Settings, get_settings
from ...db.chunk_store import get_chunk_store
from ...db.entity_store import get_entity_store
from ...db.models import KGEntity, KGMention, SourceDocument
from ...db.source_store import get_source_store
from ...domain.kg_schemas import KGLinkReport, KGRebuildReport
from ...domain.knowledge import RAW_MATERIALS, TRADE_ALIASES, resolve_material_name
from ..errors import degrade_return

logger = logging.getLogger(__name__)

_CAS_RE = re.compile(r"^(\d{2,7}-\d{2}-\d)$")


def _chem_entity_id_cas(cas: str) -> str:
    return f"chem:cas:{cas}"


def _trade_entity_id(norm_key: str) -> str:
    return f"tp:{norm_key}"


def _element_entity_id(symbol: str) -> str:
    return f"elem:{symbol.upper()}"


def _catalog_entity_id(catalog_key: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", catalog_key.lower())[:80]
    return f"chem:catalog:{safe}"


def link_source(source_id: str, *, settings: Settings | None = None) -> KGLinkReport:
    settings = settings or get_settings()
    if not settings.kg_enabled:
        return KGLinkReport(source_id=source_id)
    try:
        store = get_entity_store()
        store.delete_mentions_for_source(source_id)
        chunks = get_chunk_store().get_by_source(source_id)
        touched: set[str] = set()
        links = 0
        mentions = 0
        entities = 0

        with store._session_factory() as session:
            for chunk in chunks:
                n, eids, ln = _link_chunk(session, chunk, source_id, settings)
                mentions += n
                links += ln
                touched.update(eids)
            session.commit()

        entities = len(touched)
        if touched:
            store.refresh_counts(list(touched))

        _maybe_link_source_guide(source_id, settings)

        return KGLinkReport(
            source_id=source_id,
            entities_upserted=entities,
            mentions_upserted=mentions,
            links_created=links,
        )
    except Exception as exc:
        degrade_return(logger, exc, f"kg link_source failed for {source_id}", None)
        return KGLinkReport(source_id=source_id)


def rebuild_all(*, project_id: str | None = None, settings: Settings | None = None) -> KGRebuildReport:
    settings = settings or get_settings()
    report = KGRebuildReport()
    if not settings.kg_enabled:
        return report
    try:
        source_store = get_source_store()
        with source_store._session_factory() as session:
            q = session.query(SourceDocument.id)
            if project_id:
                q = q.filter(
                    (SourceDocument.project_id == project_id)
                    | (SourceDocument.project_id.is_(None))
                )
            source_ids = [r[0] for r in q.all()]
        for sid in source_ids:
            lr = link_source(sid, settings=settings)
            report.linked_sources += 1
            report.entities_upserted += lr.entities_upserted
            report.mentions_upserted += lr.mentions_upserted
            report.links_created += lr.links_created
        return report
    except Exception as exc:
        degrade_return(logger, exc, "kg rebuild_all failed", None)
        return report


def _link_chunk(session: Session, chunk, source_id: str, settings: Settings) -> tuple[int, set[str], int]:
    store = get_entity_store()
    touched: set[str] = set()
    mention_count = 0
    link_count = 0
    meta = chunk.meta or {}

    for item in meta.get("chem") or []:
        etype = item.get("type")
        value = item.get("value") or ""
        if not value:
            continue
        if etype == "cas" and _CAS_RE.match(value):
            eid = _chem_entity_id_cas(value)
            store.upsert_entity(
                session,
                id=eid,
                kind="chemical",
                canonical_name=value,
                cas_no=value,
                composition_status="resolved",
            )
            store.add_mention(
                session,
                entity_id=eid,
                source_id=source_id,
                chunk_id=chunk.id,
                surface_form=value,
                extractor="chem_extract",
                meta=item,
            )
            touched.add(eid)
            mention_count += 1
        elif etype == "formula":
            eid = f"chem:formula:{value}"
            symbols = _element_symbols_from_formula(value)
            store.upsert_entity(
                session,
                id=eid,
                kind="chemical",
                canonical_name=value,
                formula=value,
                element_symbols=symbols,
                composition_status="partial",
            )
            store.add_mention(
                session,
                entity_id=eid,
                source_id=source_id,
                chunk_id=chunk.id,
                surface_form=value,
                extractor="chem_extract",
                meta=item,
            )
            touched.add(eid)
            mention_count += 1
        elif etype == "smiles":
            eid = f"chem:smiles:{uuid.uuid5(uuid.NAMESPACE_URL, value).hex[:16]}"
            store.upsert_entity(
                session,
                id=eid,
                kind="chemical",
                canonical_name=value[:80],
                smiles=value,
                composition_status="resolved",
            )
            store.add_mention(
                session,
                entity_id=eid,
                source_id=source_id,
                chunk_id=chunk.id,
                surface_form=value[:80],
                extractor="chem_extract",
                meta=item,
            )
            touched.add(eid)
            mention_count += 1

    for prod in meta.get("products") or []:
        trade = (prod.get("trade_name") or "").strip()
        if not trade:
            continue
        from ...db.product_store import norm_key

        nk = norm_key(trade, prod.get("grade") or "")
        eid = _trade_entity_id(nk)
        store.upsert_entity(
            session,
            id=eid,
            kind="trade_product",
            canonical_name=trade,
            grade=prod.get("grade") or "",
            supplier=prod.get("supplier") or "",
            linked_product_key=nk,
            composition_status="unknown",
        )
        store.add_mention(
            session,
            entity_id=eid,
            source_id=source_id,
            chunk_id=chunk.id,
            surface_form=trade,
            extractor="product_store",
            meta=prod,
        )
        touched.add(eid)
        mention_count += 1
        link_count += _maybe_catalog_alias_link(
            session, eid, trade, source_id, chunk.id, settings
        )

    _link_catalog_in_text(session, chunk, source_id, touched, store)
    mention_count = (
        session.query(KGMention)
        .filter(KGMention.chunk_id == chunk.id)
        .count()
    )

    return mention_count, touched, link_count


def _element_symbols_from_formula(formula: str) -> list[str]:
    return list({m.group(1) for m in re.finditer(r"([A-Z][a-z]?)", formula or "") if m.group(1) != "V"})


def _link_catalog_in_text(session: Session, chunk, source_id: str, touched: set[str], store) -> None:
    text = chunk.text or ""
    lower = text.lower()
    for name, spec in RAW_MATERIALS.items():
        if len(name) < 4:
            continue
        if name.lower() not in lower and (spec.get("zh_name") or "") not in text:
            continue
        eid = _catalog_entity_id(name)
        store.upsert_entity(
            session,
            id=eid,
            kind="chemical",
            canonical_name=name,
            cas_no=spec.get("cas_no"),
            formula=spec.get("formula"),
            role=spec.get("role") or "",
            zh_name=spec.get("zh_name") or "",
            linked_catalog_key=name,
            composition_status="resolved",
        )
        store.add_mention(
            session,
            entity_id=eid,
            source_id=source_id,
            chunk_id=chunk.id,
            surface_form=name,
            extractor="catalog",
        )
        touched.add(eid)


def _maybe_catalog_alias_link(
    session: Session,
    trade_eid: str,
    trade: str,
    source_id: str,
    chunk_id: str,
    settings: Settings,
) -> int:
    catalog = TRADE_ALIASES.get(trade.lower()) or resolve_material_name(trade)
    if catalog == trade or catalog not in RAW_MATERIALS:
        return 0
    dst_id = _catalog_entity_id(catalog)
    spec = RAW_MATERIALS[catalog]
    store = get_entity_store()
    store.upsert_entity(
        session,
        id=dst_id,
        kind="chemical",
        canonical_name=catalog,
        cas_no=spec.get("cas_no"),
        formula=spec.get("formula"),
        linked_catalog_key=catalog,
        composition_status="resolved",
    )
    if settings.kg_trade_product_link_min_conf <= 0.85:
        pass
    store.add_link(
        session,
        src_entity_id=trade_eid,
        dst_entity_id=dst_id,
        link_type="catalog_alias",
        confidence=0.95,
        evidence_refs=[{"source_id": source_id, "chunk_id": chunk_id}],
    )
    return 1


def _maybe_link_source_guide(source_id: str, settings: Settings) -> None:
    if not settings.kg_llm_product_hint:
        return
    try:
        from ...db.source_store import get_source_store

        with get_source_store()._session_factory() as session:
            doc = session.get(SourceDocument, source_id)
            if not doc or not doc.source_guide:
                return
            products = doc.source_guide.get("products") or []
        store = get_entity_store()
        with store._session_factory() as session:
            for p in products:
                trade = (p.get("trade_name") or "").strip()
                if not trade:
                    continue
                from ...db.product_store import norm_key

                eid = _trade_entity_id(norm_key(trade, p.get("grade") or ""))
                hints = p.get("generic_name") or p.get("generic_name_hint") or ""
                store.upsert_entity(
                    session,
                    id=eid,
                    kind="trade_product",
                    canonical_name=trade,
                    generic_name_hint=hints[:256],
                    composition_status="partial" if hints else "unknown",
                )
            session.commit()
    except Exception as exc:
        degrade_return(logger, exc, "source_guide kg hint failed", None)
