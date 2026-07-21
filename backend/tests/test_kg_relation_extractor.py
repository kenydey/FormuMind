"""KG-R1 — semantic relation extraction and link storage."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.db.models import KGEntity, KGEntityLink, KGMention
from app.domain.kg_schemas import RelationType
from app.services.kg.relation_extractor import extract_relations_from_chunk


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def entity_store(tmp_path, monkeypatch):
    import app.db.entity_store as entity_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kg_rel.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    monkeypatch.setattr(entity_store_mod, "_store", store)
    return store


def _mention(entity_id: str, chunk_id: str, surface: str) -> KGMention:
    return KGMention(
        id=f"m-{entity_id}-{surface[:8]}",
        entity_id=entity_id,
        source_id="s1",
        chunk_id=chunk_id,
        surface_form=surface,
    )


def _entity(eid: str, name: str, *, zh: str = "", cas: str | None = None) -> KGEntity:
    return KGEntity(
        id=eid,
        kind="chemical",
        canonical_name=name,
        zh_name=zh,
        cas_no=cas,
        composition_status="resolved",
    )


def test_rule_extract_substitutes_and_synergizes(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KG_RELATION_EXTRACT_ENABLED", "true")
    get_settings.cache_clear()

    zinc = "chem:catalog:zinc_phosphate"
    chrome = "chem:catalog:chromate"
    epoxy = "chem:catalog:epoxy"
    mentions = [
        _mention(zinc, "c1", "磷酸锌"),
        _mention(chrome, "c1", "铬酸盐颜料"),
        _mention(epoxy, "c1", "环氧树脂"),
    ]
    entities = {
        zinc: _entity(zinc, "Zinc phosphate", zh="磷酸锌"),
        chrome: _entity(chrome, "Chromate pigment", zh="铬酸盐颜料"),
        epoxy: _entity(epoxy, "Epoxy resin", zh="环氧树脂"),
    }
    text = "磷酸锌可替代铬酸盐颜料，与环氧树脂协同提升耐盐雾性能。"
    rels = extract_relations_from_chunk(
        text,
        mentions,
        entities,
        source_id="s1",
        chunk_id="c1",
    )
    types = {r.relation_type for r in rels}
    assert RelationType.SUBSTITUTES in types
    assert RelationType.SYNERGIZES in types
    sub = next(r for r in rels if r.relation_type == RelationType.SUBSTITUTES)
    assert sub.source_entity_id == zinc
    assert sub.target_entity_id == chrome


def test_merge_semantic_link_deduplicates_evidence(entity_store):
    with entity_store._session_factory() as session:
        entity_store.upsert_entity(
            session,
            id="chem:a",
            kind="chemical",
            canonical_name="A",
            composition_status="resolved",
        )
        entity_store.upsert_entity(
            session,
            id="chem:b",
            kind="chemical",
            canonical_name="B",
            composition_status="resolved",
        )
        ev = {
            "source_id": "s1",
            "chunk_id": "c1",
            "sentence": "A 替代 B",
            "confidence": 0.62,
            "extraction_method": "rule",
        }
        assert entity_store.merge_semantic_link(
            session,
            src_entity_id="chem:a",
            dst_entity_id="chem:b",
            link_type="substitutes",
            confidence=0.62,
            evidence_ref=ev,
        )
        assert entity_store.merge_semantic_link(
            session,
            src_entity_id="chem:a",
            dst_entity_id="chem:b",
            link_type="substitutes",
            confidence=0.7,
            evidence_ref=ev,
        )
        session.commit()
        rows = session.query(KGEntityLink).all()
        assert len(rows) == 1
        assert len(rows[0].evidence_refs) == 1
        assert rows[0].confidence == 0.7


def test_delete_links_for_source_preserves_catalog_alias(entity_store):
    with entity_store._session_factory() as session:
        entity_store.add_link(
            session,
            src_entity_id="tp:x",
            dst_entity_id="chem:y",
            link_type="catalog_alias",
            confidence=0.95,
            evidence_refs=[{"source_id": "s1", "chunk_id": "c1"}],
        )
        entity_store.merge_semantic_link(
            session,
            src_entity_id="chem:a",
            dst_entity_id="chem:b",
            link_type="substitutes",
            confidence=0.62,
            evidence_ref={
                "source_id": "s1",
                "chunk_id": "c1",
                "sentence": "A 替代 B",
                "confidence": 0.62,
                "extraction_method": "rule",
            },
        )
        session.commit()
    removed = entity_store.delete_links_for_source("s1")
    assert removed == 1
    with entity_store._session_factory() as session:
        remaining = session.query(KGEntityLink).all()
        assert len(remaining) == 1
        assert remaining[0].link_type == "catalog_alias"


def test_link_source_relations_integration(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KG_RELATION_EXTRACT_ENABLED", "true")
    get_settings.cache_clear()

    import app.db.chunk_store as chunk_store_mod
    import app.db.entity_store as entity_store_mod
    import app.db.source_store as source_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.source_store import SourceStore
    from app.services import kb_index
    from app.services.kg.entity_linker import link_source

    engine = make_engine(f"sqlite:///{tmp_path}/kg_int.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    ent = EntityStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(entity_store_mod, "_store", ent)

    md = (
        "# Anticorrosion\n\n"
        "Zinc phosphate synergizes with Bisphenol-A epoxy (DGEBA) for salt spray performance.\n"
        "Zinc phosphate 8 wt%, CAS 7779-90-0.\n"
    )
    sid = src.create(
        filename="p.md",
        title="防腐",
        source_kind="patent",
        full_text=md,
        content_hash="rel-h1",
    )
    kb_index.index_source(sid, md, embed=False)
    report = link_source(sid)
    assert report.relations_upserted >= 1
    stats = ent.stats()
    semantic = sum(
        stats["links_by_type"].get(t, 0)
        for t in ("substitutes", "synergizes", "inhibits", "correlates_pos", "correlates_neg", "requires")
    )
    assert semantic >= 1
