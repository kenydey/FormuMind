"""Phase 1 深度研究引擎测试 — 离线安全。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.deep_research import (
    DeepResearchEngine,
    DocumentType,
    QueryExpander,
    ResearchResult,
)
from app.services import literature

client = TestClient(app)

_REQUIREMENT = {
    "domain": "anticorrosion_coating",
    "substrate": "carbon_steel",
    "salt_spray_hours": 500,
    "film_weight_gsm": 70,
    "cure_temperature_c": 80,
    "cleaning_efficiency": 90,
    "voc_limit_gpl": 420,
    "ph_target": None,
    "notes": "",
    "objectives": [],
}


def test_build_patent_query_combines_user_query_and_headline():
    req = literature.Requirement(**_REQUIREMENT)
    q = literature._build_patent_query(req, "水性聚氨酯防腐涂料")
    assert "水性聚氨酯防腐涂料" in q
    assert "anticorrosion_coating" in q


def test_query_expander_offline_returns_valid_structure():
    expanded = QueryExpander().expand("水性聚氨酯防腐涂料")
    assert expanded.intent
    assert expanded.chinese_keywords
    assert expanded.ipc_cpc_suggestions


def test_research_result_evidence_roundtrip():
    original = literature.Evidence(
        source="USPTO",
        identifier="US9982145B2",
        title="Waterborne epoxy anticorrosive coating",
        snippet="zinc phosphate primer",
        relevance=0.9,
    )
    rr = ResearchResult.from_evidence(original)
    assert rr.doc_type == DocumentType.PATENT
    assert rr.title == original.title
    ev = rr.to_evidence()
    assert ev.identifier == original.identifier
    assert ev.snippet == original.snippet


def test_deep_research_engine_search_offline():
    req = literature.Requirement(**_REQUIREMENT)
    with DeepResearchEngine() as engine:
        report = engine.search(
            "zinc phosphate epoxy",
            source_types=["patents"],
            req=req,
            total_limit=50,
        )
    assert report.topic == "zinc phosphate epoxy"
    assert report.expanded_query is not None
    assert len(report.results) >= 1
    assert report.source_counts


def test_expand_api_endpoint():
    r = client.get("/api/research/expand", params={"topic": "水性聚氨酯防腐涂料"})
    assert r.status_code == 200
    body = r.json()
    assert "chinese_keywords" in body
    assert "ipc_cpc_suggestions" in body
