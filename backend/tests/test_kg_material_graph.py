"""P2: materials KG Hub canvas graph API (SQLite; Neo4j not required)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def entity_store(tmp_path, monkeypatch):
    import app.db.entity_store as entity_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kg_hub_graph.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    monkeypatch.setattr(entity_store_mod, "_store", store)
    return store


def _seed(entity_store: EntityStore) -> None:
    with entity_store._session_factory() as session:
        for eid, name in (
            ("chem:a", "Chromate"),
            ("chem:b", "Zinc phosphate"),
            ("chem:c", "Epoxy"),
            ("chem:lonely", "Lonely Chem"),
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
            confidence=0.9,
            evidence_ref={"source_id": "s1", "sentence": "B replaces A", "confidence": 0.9},
        )
        entity_store.merge_semantic_link(
            session,
            src_entity_id="chem:b",
            dst_entity_id="chem:c",
            link_type="synergizes",
            confidence=0.7,
            evidence_ref={"source_id": "s1", "sentence": "B with C", "confidence": 0.7},
        )
        entity_store.merge_semantic_link(
            session,
            src_entity_id="chem:a",
            dst_entity_id="chem:c",
            link_type="measured_performance",
            confidence=0.85,
            evidence_ref={
                "source_id": "s2",
                "sentence": "measured",
                "confidence": 0.85,
                "extraction_method": "measured",
            },
            extraction_method="measured",
        )
        session.commit()


def test_kg_graph_flag_off_409(entity_store, monkeypatch):
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "false")
    get_settings.cache_clear()
    client = TestClient(app)
    r = client.get("/api/kg/graph")
    assert r.status_code == 409


def test_kg_graph_default_filters_and_projection(entity_store):
    _seed(entity_store)
    client = TestClient(app)
    # default substitutes + measured_*
    r = client.get("/api/kg/graph", params={"limit": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["meta"]["backend"] == "sqlite"
    types = set(body["meta"]["relation_types"])
    assert "substitutes" in types
    assert "measured_performance" in types
    # synergizes excluded by default filter
    rels = {e["relation_type"] for e in body["edges"]}
    assert "substitutes" in rels
    assert "measured_performance" in rels
    assert "synergizes" not in rels
    ids = {n["id"] for n in body["nodes"]}
    assert "chem:a" in ids and "chem:b" in ids
    # lonely chem has no selected-type edges → not in graph
    assert "chem:lonely" not in ids


def test_kg_graph_all_semantic_includes_synergizes(entity_store):
    _seed(entity_store)
    client = TestClient(app)
    r = client.get(
        "/api/kg/graph",
        params={"relation_types": "substitutes,synergizes,measured_performance", "limit": 50},
    )
    assert r.status_code == 200
    rels = {e["relation_type"] for e in r.json()["edges"]}
    assert "synergizes" in rels
    by_id = {n["id"]: n for n in r.json()["nodes"]}
    assert by_id["chem:b"]["degree_out"] >= 2
