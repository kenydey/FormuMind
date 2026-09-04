"""KG v10 — literature↔measured contradiction detection + discover demotion."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore, SEMANTIC_LINK_TYPES
from app.services.kg.contradiction import (
    detect_contradictions,
    detect_contradictions_by_query,
)
from app.services.kg.graph_query import discover_substitutes


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    import app.db.entity_store as entity_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kg_contra.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    es = EntityStore(factory)
    monkeypatch.setattr(entity_store_mod, "_store", es)
    return es


def _seed(store: EntityStore, sub_id: str, prop_id: str,
          lit_conf: float = 0.8, meas_val: float = 0.1):
    """domain=chem:d 文献 substitutes->sub_id；实测 chem:d->prop_id 低分（矛盾）。"""
    with store._session_factory() as session:
        for eid, name in (("chem:d", "Polymer A"), (sub_id, "Sub X"),
                          (prop_id, "Adhesion")):
            store.upsert_entity(session, id=eid, kind="chemical",
                                canonical_name=name, composition_status="resolved")
        store.merge_semantic_link(
            session, src_entity_id="chem:d", dst_entity_id=sub_id,
            link_type="substitutes", confidence=lit_conf,
            evidence_ref={"source_id": "s1", "chunk_id": "c1",
                          "sentence": "A can be replaced by X", "confidence": lit_conf,
                          "extraction_method": "rule"},
        )
        # measured_performance: domain->property, low value => contradiction
        store.merge_semantic_link(
            session, src_entity_id="chem:d", dst_entity_id=prop_id,
            link_type="measured_performance", confidence=meas_val,
            evidence_ref={"source_id": "measured:campaign_7",
                          "extraction_method": "measured",
                          "sentence": f"实测 {prop_id}={meas_val}"},
            extraction_method="measured",
        )
        session.commit()


def test_no_measured_returns_empty(store):
    """实体无实测边 → 矛盾列表为空（不报错）。"""
    with store._session_factory() as session:
        store.upsert_entity(session, id="chem:z", kind="chemical",
                            canonical_name="Z", composition_status="resolved")
        store.merge_semantic_link(
            session, src_entity_id="chem:z", dst_entity_id="chem:w",
            link_type="substitutes", confidence=0.9,
            evidence_ref={"source_id": "s", "chunk_id": "c",
                          "sentence": "z replaces w", "confidence": 0.9,
                          "extraction_method": "rule"},
        )
        session.commit()
    resp = detect_contradictions("chem:z")
    assert resp.contradictions == []


def test_substitute_vs_poor_detected(store):
    """文献 substitutes + 实测该替代物属性差 → 标记 substitute_vs_poor。"""
    _seed(store, "chem:sub", "chem:adh")
    resp = detect_contradictions("chem:d")
    marks = [m for m in resp.contradictions if m.target_entity_id == "chem:sub"]
    assert marks, "应检测到 chem:sub 的矛盾"
    assert marks[0].contradiction_type == "substitute_vs_poor"
    assert marks[0].strength >= get_settings().kg_contradiction_threshold


def test_synergy_vs_poor_detected(store):
    """文献 synergizes + 实测属性低 → synergy_vs_poor。"""
    with store._session_factory() as session:
        for eid, name in (("chem:d", "Polymer A"), ("chem:s", "Syn"),
                          ("chem:adh", "Adhesion")):
            store.upsert_entity(session, id=eid, kind="chemical",
                                canonical_name=name, composition_status="resolved")
        store.merge_semantic_link(
            session, src_entity_id="chem:d", dst_entity_id="chem:s",
            link_type="synergizes", confidence=0.85,
            evidence_ref={"source_id": "s", "chunk_id": "c",
                          "sentence": "synergy", "confidence": 0.85,
                          "extraction_method": "rule"},
        )
        store.merge_semantic_link(
            session, src_entity_id="chem:d", dst_entity_id="chem:adh",
            link_type="measured_performance", confidence=0.1,
            evidence_ref={"source_id": "measured:campaign_9",
                          "extraction_method": "measured", "sentence": "low"},
            extraction_method="measured",
        )
        session.commit()
    resp = detect_contradictions("chem:d")
    syn = [m for m in resp.contradictions if m.target_entity_id == "chem:s"]
    assert syn and syn[0].contradiction_type == "synergy_vs_poor"


def test_threshold_filters_weak(store, monkeypatch):
    """实测偏离低于阈值 → 不标记。"""
    get_settings().kg_contradiction_threshold = 0.99
    _seed(store, "chem:sub", "chem:adh", lit_conf=0.8, meas_val=0.45)
    resp = detect_contradictions("chem:d")
    assert resp.contradictions == []


def test_resolve_by_query(store):
    """detect_contradictions_by_query 经 resolve 命中。"""
    _seed(store, "chem:sub", "chem:adh")
    resp = detect_contradictions_by_query("Polymer A")
    assert any(m.target_entity_id == "chem:sub" for m in resp.contradictions)


def test_discover_demotes_contradicted(store, monkeypatch):
    """冲突候选在 discover_substitutes 中 contradiction_flag=True 且置后。"""
    get_settings().kg_contradiction_demote = True
    _seed(store, "chem:sub", "chem:adh", lit_conf=0.9, meas_val=0.05)
    result = discover_substitutes("chem:d", limit=10)
    flagged = [c for c in result.substitutes if c.contradiction_flag]
    assert flagged, "应有冲突候选被打标"
    assert flagged[0].contradiction_detail == "substitute_vs_poor"
    # 置后：flagged 候选的索引应普遍靠后（非全部前置）
    last = result.substitutes[-1]
    assert last.contradiction_flag


def test_api_contradictions_endpoint(store):
    """GET /api/kg/contradictions 返回结构化矛盾。"""
    from app.main import app

    _seed(store, "chem:sub", "chem:adh")
    client = TestClient(app)
    r = client.get("/api/kg/contradictions", params={"entity_id": "chem:d"})
    assert r.status_code == 200
    body = r.json()
    assert any(m["target_entity_id"] == "chem:sub" for m in body["contradictions"])


def test_average_trap_two_props_detected_via_worst(store):
    """P1: 两属性实测 0.1(防腐)+0.9(光泽) → 旧全局平均 0.5 必漏报;
    新逻辑用最差属性 0.1 → 文献 substitutes 矛盾必命中, 归因到低分属性。"""
    with store._session_factory() as session:
        for eid, name in (("chem:d", "Polymer A"), ("chem:sub", "Sub X"),
                          ("prop:nss", "耐盐雾"), ("prop:gloss", "光泽度")):
            store.upsert_entity(session, id=eid, kind="chemical",
                                canonical_name=name, composition_status="resolved")
        store.merge_semantic_link(
            session, src_entity_id="chem:d", dst_entity_id="chem:sub",
            link_type="substitutes", confidence=0.9,
            evidence_ref={"source_id": "s1", "chunk_id": "c1",
                          "sentence": "A can be replaced by X", "confidence": 0.9,
                          "extraction_method": "rule"},
        )
        # 防腐 0.1(差) + 光泽 0.9(好) —— 平均值 0.5 会漏报
        store.merge_semantic_link(
            session, src_entity_id="chem:d", dst_entity_id="prop:nss",
            link_type="measured_performance", confidence=0.1,
            evidence_ref={"source_id": "measured:c1", "extraction_method": "measured",
                          "sentence": "NSS=0.1"}, extraction_method="measured",
        )
        store.merge_semantic_link(
            session, src_entity_id="chem:d", dst_entity_id="prop:gloss",
            link_type="measured_performance", confidence=0.9,
            evidence_ref={"source_id": "measured:c2", "extraction_method": "measured",
                          "sentence": "gloss=0.9"}, extraction_method="measured",
        )
        session.commit()
    resp = detect_contradictions("chem:d")
    marks = [m for m in resp.contradictions if m.target_entity_id == "chem:sub"]
    assert marks, "平均值 0.5 场景必须检出矛盾(最差属性 0.1)"
    assert marks[0].measured_property == "prop:nss", "归因应为低分属性"
    assert marks[0].measured_value == 0.1


def test_inhibit_claim_uses_best_property(store):
    """P1: expected_sign=-1(inhibits, 声称性能差)须用最好属性对标——
    实测 0.1/0.9 时旧平均 0.5 同样漏报; 新逻辑 best=0.9 → inhibit_vs_good 命中。"""
    with store._session_factory() as session:
        for eid, name in (("chem:d", "Polymer A"), ("chem:inh", "Inhib X"),
                          ("prop:nss", "耐盐雾"), ("prop:gloss", "光泽度")):
            store.upsert_entity(session, id=eid, kind="chemical",
                                canonical_name=name, composition_status="resolved")
        store.merge_semantic_link(
            session, src_entity_id="chem:d", dst_entity_id="chem:inh",
            link_type="inhibits", confidence=0.9,
            evidence_ref={"source_id": "s1", "chunk_id": "c1",
                          "sentence": "X inhibits A", "confidence": 0.9,
                          "extraction_method": "rule"},
        )
        store.merge_semantic_link(
            session, src_entity_id="chem:d", dst_entity_id="prop:nss",
            link_type="measured_performance", confidence=0.1,
            evidence_ref={"source_id": "measured:c1", "extraction_method": "measured",
                          "sentence": "NSS=0.1"}, extraction_method="measured",
        )
        store.merge_semantic_link(
            session, src_entity_id="chem:d", dst_entity_id="prop:gloss",
            link_type="measured_performance", confidence=0.9,
            evidence_ref={"source_id": "measured:c2", "extraction_method": "measured",
                          "sentence": "gloss=0.9"}, extraction_method="measured",
        )
        session.commit()
    resp = detect_contradictions("chem:d")
    marks = [m for m in resp.contradictions if m.target_entity_id == "chem:inh"]
    assert marks, "inhibits 声称 + 实测有属性 0.9 必须检出矛盾(旧平均 0.5 漏报)"
    assert marks[0].contradiction_type == "inhibit_vs_good"
    assert marks[0].measured_property == "prop:gloss", "归因应为最好属性"
    assert marks[0].measured_value == 0.9
