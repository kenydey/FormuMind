"""P1 KG provenance 可观测：过滤 + feedback stats + sync kg_written."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import SqliteCampaignStore, reset_campaign_store
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.domain.schemas import DOEPlan, DOERun, ProductDomain, Requirement, ObjectiveSpec
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_campaign_store(None)


def _entity_store(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path}/kgprov.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    import app.db.entity_store as es_mod

    monkeypatch.setattr(es_mod, "_store", store)
    return store


def test_graph_query_filter_by_extraction_method(tmp_path, monkeypatch):
    store = _entity_store(tmp_path, monkeypatch)
    with store._session_factory() as s:
        store.upsert_entity(s, id="dom", canonical_name="anticorrosion_coating", kind="domain")
        store.upsert_entity(s, id="propA", canonical_name="salt_spray_hours", kind="property")
        store.upsert_entity(s, id="propB", canonical_name="cost_cny_per_kg", kind="property")
    with store._session_factory() as s:
        store.merge_semantic_link(s, src_entity_id="dom", dst_entity_id="propA", link_type="measured_performance", confidence=0.7, evidence_ref={"source_id": "lit-1", "extraction_method": "rule", "sentence": "lit"}, extraction_method="rule")
        store.merge_semantic_link(s, src_entity_id="dom", dst_entity_id="propB", link_type="measured_performance", confidence=0.8, evidence_ref={"source_id": "measured:campaign_1", "extraction_method": "measured", "sentence": "实测 750"}, extraction_method="measured")

    from app.services.kg.graph_query import get_entity_relations

    all_rels = get_entity_relations("dom", limit=10)
    assert len(all_rels) == 2
    measured = get_entity_relations("dom", extraction_method="measured", limit=10)
    assert all((r.extraction_method == "measured" or any(e.extraction_method == "measured" for e in r.evidence)) for r in measured)
    assert any("propB" in (r.target_entity_id + r.source_entity_id) for r in measured)
    rule_only = get_entity_relations("dom", extraction_method="rule", limit=10)
    assert any("propA" in (r.target_entity_id + r.source_entity_id) for r in rule_only)
    assert all((r.extraction_method == "rule" or any(e.extraction_method == "rule" for e in r.evidence)) for r in rule_only)


def test_feedback_stats_endpoint(tmp_path, monkeypatch):
    store = _entity_store(tmp_path, monkeypatch)
    with store._session_factory() as s:
        store.upsert_entity(s, id="dom", canonical_name="anticorrosion_coating", kind="domain")
        store.upsert_entity(s, id="p1", canonical_name="m1", kind="property")
        store.upsert_entity(s, id="p2", canonical_name="m2", kind="property")
    with store._session_factory() as s:
        store.merge_semantic_link(s, src_entity_id="dom", dst_entity_id="p1", link_type="measured_performance", confidence=0.6, evidence_ref={"source_id": "measured:campaign_1", "extraction_method": "measured", "sentence": "x"}, extraction_method="measured")
        store.merge_semantic_link(s, src_entity_id="dom", dst_entity_id="p2", link_type="measured_performance", confidence=0.6, evidence_ref={"source_id": "measured:campaign_2", "extraction_method": "measured", "sentence": "y"}, extraction_method="measured")
        store.merge_semantic_link(s, src_entity_id="dom", dst_entity_id="p1", link_type="correlates_pos", confidence=0.5, evidence_ref={"source_id": "lit-1", "extraction_method": "rule", "sentence": "lit"}, extraction_method="rule")

    client = TestClient(app)
    r = client.get("/api/kg/feedback/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["measured_performance"] == 2
    assert body["measured_total"] >= 2
    assert "measured:campaign_1" in body["by_campaign"]


def test_sync_returns_kg_written(tmp_path, monkeypatch):
    # 端到端：sync 透传 kg_written，且 feedback stats 随之增长
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    engine = make_engine(f"sqlite:///{tmp_path}/sync_kg.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    # KG store 共享同一 factory（entity_store 使用同一 DB 文件便于测试）
    store_ent = EntityStore(factory)
    import app.db.entity_store as es_mod

    monkeypatch.setattr(es_mod, "_store", store_ent)
    with store_ent._session_factory() as s:
        store_ent.upsert_entity(s, id="dom", canonical_name="anticorrosion_coating", kind="domain")
    reset_campaign_store(SqliteCampaignStore(factory))
    client = TestClient(app)
    plan = DOEPlan(design="lhs", factors=[], runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 9.0, "cure_temperature_c": 82.0})], notes="t", plan_id="kgw", domain=ProductDomain.anticorrosion_coating)
    req = Requirement(domain=ProductDomain.anticorrosion_coating, objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")])
    r = client.post("/api/experiments/workbench/campaigns", json={"plan": plan.model_dump(), "requirement": req.model_dump()})
    assert r.status_code == 200
    cid = r.json()["campaign_id"]
    row = r.json()["rows"][0]
    r2 = client.put("/api/experiments/workbench/sync", json={"campaign_id": cid, "rows": [{"id": row["id"], "status": "Completed", "actual_params": {"Zinc phosphate": 9.0}, "measurements": {"salt_spray_hours": 780.0}}], "requirement": req.model_dump()})
    assert r2.status_code == 200
    body = r2.json()
    assert "kg_written" in body
    assert body["kg_written"] is not None and body["kg_written"] >= 1
    # feedback stats 应反映
    r3 = client.get("/api/kg/feedback/stats")
    assert r3.status_code == 200
    assert r3.json()["measured_performance"] >= 1
