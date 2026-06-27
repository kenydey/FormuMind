"""Phase A search query differentiation and merge/rank filtering."""
from __future__ import annotations

from app.domain.schemas import Evidence
from app.services.deep_research.models import ExpandedQuery
from app.services.deep_research.query_expander import (
    build_chinese_query,
    build_patent_query,
    build_rank_query,
    build_western_query,
    prepare_search_queries,
)
from app.services import literature


def _expanded() -> ExpandedQuery:
    return ExpandedQuery(
        intent="水性防腐涂料",
        chinese_keywords=["水性", "防腐涂料", "环氧底漆"],
        english_synonyms=["waterborne", "anticorrosive coating", "epoxy primer"],
        ipc_cpc_suggestions=["C09D", "C09D175/04", "C08G18/00"],
    )


def test_build_rank_query_duplicates_ipc_for_weight():
    eq = _expanded()
    q = build_rank_query(eq, "zinc phosphate")
    assert q.count("C09D") == 2
    assert "waterborne" in q
    assert "水性" in q


def test_build_patent_query_english_heavy():
    eq = _expanded()
    q = build_patent_query(eq, "zinc phosphate epoxy")
    assert "zinc phosphate epoxy" in q
    assert "waterborne" in q
    # Chinese keywords should not dominate patent full-text query
    assert "防腐涂料" not in q or "anticorrosive" in q


def test_build_western_and_chinese_queries():
    eq = _expanded()
    assert "waterborne" in build_western_query(eq, "fallback topic")
    assert "水性" in build_chinese_query(eq, "")


def test_prepare_search_queries_offline():
    sq = prepare_search_queries("水性聚氨酯防腐涂料")
    assert sq.rank_q
    assert sq.patent_q
    assert sq.expanded.chinese_keywords
    assert sq.ipc_codes


def test_merge_filter_drops_irrelevant_literature():
    q = "zinc phosphate epoxy primer"
    relevant = Evidence(
        source="arXiv",
        identifier="arxiv:1",
        title="Zinc phosphate epoxy anticorrosive primer",
        snippet="corrosion protection",
        relevance=0.8,
    )
    junk = Evidence(
        source="arXiv",
        identifier="arxiv:2",
        title="Quantum computing advances",
        snippet="unrelated physics",
        relevance=0.9,
    )
    merged = literature._merge_filter_rank([junk, relevant], q, 10)
    ids = {e.identifier for e in merged}
    assert "arxiv:1" in ids
    assert "arxiv:2" not in ids


def test_expand_api_returns_per_source_queries():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.get("/api/search/expand", params={"topic": "水性聚氨酯防腐涂料"})
    assert r.status_code == 200
    body = r.json()
    for key in ("rank_q", "patent_q", "western_q", "chinese_q", "ipc_codes"):
        assert key in body
