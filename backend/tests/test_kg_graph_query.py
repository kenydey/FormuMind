"""KG-R2 — graph query API and LLM extraction."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.domain.kg_schemas import RelationType
from app.services.kg.graph_query import discover_substitutes, find_path, get_entity_relations
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

    engine = make_engine(f"sqlite:///{tmp_path}/kg_graph.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    monkeypatch.setattr(entity_store_mod, "_store", store)
    return store


def _seed_substitute_chain(entity_store: EntityStore) -> tuple[str, str, str]:
    with entity_store._session_factory() as session:
        for eid, name in (
            ("chem:a", "Chromate"),
            ("chem:b", "Zinc phosphate"),
            ("chem:c", "Epoxy"),
        ):
            entity_store.upsert_entity(
                session,
                id=eid,
                kind="chemical",
                canonical_name=name,
                composition_status="resolved",
            )
        entity_store.merge_semantic_link(
            session,
            src_entity_id="chem:b",
            dst_entity_id="chem:a",
            link_type="substitutes",
            confidence=0.8,
            evidence_ref={
                "source_id": "s1",
                "chunk_id": "c1",
                "sentence": "Zinc phosphate replaces chromate",
                "confidence": 0.8,
                "extraction_method": "rule",
            },
        )
        entity_store.merge_semantic_link(
            session,
            src_entity_id="chem:b",
            dst_entity_id="chem:c",
            link_type="synergizes",
            confidence=0.7,
            evidence_ref={
                "source_id": "s1",
                "chunk_id": "c1",
                "sentence": "Zinc phosphate synergizes with epoxy",
                "confidence": 0.7,
                "extraction_method": "rule",
            },
        )
        session.commit()
    return "chem:a", "chem:b", "chem:c"


def test_llm_extract_merges_with_rules(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KG_RELATION_EXTRACT_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KG_LLM_RELATION_EXTRACT", "true")
    get_settings.cache_clear()

    from app.db.models import KGEntity, KGMention

    zinc = "chem:catalog:zinc_phosphate"
    chrome = "chem:catalog:chromate"
    mentions = [
        KGMention(
            id="m1",
            entity_id=zinc,
            source_id="s1",
            chunk_id="c1",
            surface_form="磷酸锌",
        ),
        KGMention(
            id="m2",
            entity_id=chrome,
            source_id="s1",
            chunk_id="c1",
            surface_form="铬酸盐",
        ),
    ]
    entities = {
        zinc: KGEntity(
            id=zinc,
            kind="chemical",
            canonical_name="Zinc phosphate",
            zh_name="磷酸锌",
            composition_status="resolved",
        ),
        chrome: KGEntity(
            id=chrome,
            kind="chemical",
            canonical_name="Chromate",
            zh_name="铬酸盐",
            composition_status="resolved",
        ),
    }

    def fake_llm(_prompt: str):
        return {
            "relations": [
                {
                    "source_entity_id": zinc,
                    "target_entity_id": chrome,
                    "relation_type": "requires",
                    "sentence": "磷酸锌需要铬酸盐助剂",
                    "confidence": 0.71,
                }
            ]
        }

    monkeypatch.setattr("app.services.llm.complete_json", fake_llm)
    text = "磷酸锌可替代铬酸盐颜料。"
    rels = extract_relations_from_chunk(
        text,
        mentions,
        entities,
        source_id="s1",
        chunk_id="c1",
    )
    types = {r.relation_type for r in rels}
    assert RelationType.SUBSTITUTES in types
    assert RelationType.REQUIRES in types


def test_graph_query_relations_and_path(entity_store):
    chromate, zinc, epoxy = _seed_substitute_chain(entity_store)
    rels = get_entity_relations(zinc, limit=10)
    assert len(rels) >= 2
    path = find_path(zinc, epoxy, max_depth=3)
    assert path.found
    assert path.hops >= 1


def test_discover_substitutes(entity_store):
    chromate, zinc, _ = _seed_substitute_chain(entity_store)
    result = discover_substitutes(chromate, limit=5)
    assert result.query_entity_id == chromate
    assert any(c.entity_id == zinc for c in result.substitutes)


def test_synergy_neighbours_are_not_substitutes(entity_store):
    """A synergist is not a replacement. chem:c synergises with chem:b; it must
    never surface as something you could swap chem:a for."""
    chromate, _, epoxy = _seed_substitute_chain(entity_store)
    result = discover_substitutes(chromate, limit=10)
    assert epoxy not in {c.entity_id for c in result.substitutes}


def test_synergy_edges_do_not_crowd_out_real_substitutes(entity_store):
    """The second hop fetches ORDER BY confidence DESC LIMIT 20 and filters
    afterwards. While synergy edges were included in that request they ate the
    budget at higher confidence, so genuine substitutes fell off the end."""
    with entity_store._session_factory() as session:
        for i in range(20):
            entity_store.upsert_entity(
                session, id=f"chem:syn{i}", kind="chemical",
                canonical_name=f"Synergist {i}", composition_status="resolved",
            )
            entity_store.merge_semantic_link(
                session, src_entity_id="chem:b", dst_entity_id=f"chem:syn{i}",
                link_type="synergizes", confidence=0.99,
                evidence_ref={"source_id": "s", "chunk_id": "c",
                              "sentence": "synergy", "confidence": 0.99,
                              "extraction_method": "rule"},
            )
        entity_store.upsert_entity(
            session, id="chem:deep", kind="chemical",
            canonical_name="Deep substitute", composition_status="resolved",
        )
        # Low confidence on purpose: it must still be reachable.
        entity_store.merge_semantic_link(
            session, src_entity_id="chem:b", dst_entity_id="chem:deep",
            link_type="substitutes", confidence=0.2,
            evidence_ref={"source_id": "s", "chunk_id": "c",
                          "sentence": "b can be replaced by deep", "confidence": 0.2,
                          "extraction_method": "rule"},
        )
        session.commit()

    chromate, _, _ = _seed_substitute_chain(entity_store)
    found = {c.entity_id for c in discover_substitutes(chromate, limit=10).substitutes}
    assert "chem:deep" in found
    assert not any(eid.startswith("chem:syn") for eid in found)


def test_kg_graph_api(entity_store, monkeypatch):
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    get_settings.cache_clear()
    chromate, zinc, epoxy = _seed_substitute_chain(entity_store)

    from app.main import app

    client = TestClient(app)
    rel_resp = client.get(f"/api/kg/relations/{zinc}")
    assert rel_resp.status_code == 200
    assert len(rel_resp.json()) >= 2

    path_resp = client.get(f"/api/kg/path?src={zinc}&dst={epoxy}")
    assert path_resp.status_code == 200
    assert path_resp.json()["found"] is True

    sub_resp = client.get(f"/api/kg/discover/substitutes?entity_id={chromate}")
    assert sub_resp.status_code == 200
    assert sub_resp.json()["substitutes"]

    resolve_resp = client.get("/api/kg/resolve?q=Zinc")
    assert resolve_resp.status_code == 200
    assert "top_relations" in resolve_resp.json()
