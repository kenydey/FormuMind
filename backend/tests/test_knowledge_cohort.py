"""Tests for v0.9 advanced RAG + knowledge-cohort deep research.

All assertions run fully offline (no intel/embedding extras, no LLM key): the
cohort must still return a citation-grounded report from the seed corpus, and
the HyDE / re-rank helpers must be behaviour-preserving no-ops without an LLM.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.domain.schemas import Evidence, Requirement
from app.services import knowledge_cohort, literature, rag

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Keep the cohort deterministic and offline.

    With the intel extra installed, ``web_agent`` would hit DuckDuckGo over the
    network. Stub the internet branch so tests exercise the orchestration /
    fallback logic without depending on external services.
    """
    real_search_by_types = literature.search_by_types

    def fake_search_by_types(query, source_types, **kwargs):
        if source_types == ["internet"]:
            return []  # no web results in tests
        return real_search_by_types(query, source_types, **kwargs)

    monkeypatch.setattr(literature, "search_by_types", fake_search_by_types)

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


def _evidence(n: int) -> list[Evidence]:
    return [
        Evidence(
            source="USPTO",
            identifier=f"US{i}",
            title=f"Zinc phosphate epoxy primer {i}",
            snippet=f"Waterborne epoxy with zinc phosphate inhibitor, variant {i}.",
            relevance=1.0 - i * 0.1,
        )
        for i in range(n)
    ]


# ── HyDE / re-rank graceful fallback ─────────────────────────────────────────

def test_hyde_expand_offline_returns_query():
    # No LLM configured in CI → expansion returns the original query unchanged.
    q = "low-temperature curing anti-corrosion coating"
    assert rag.hyde_expand(q) == q


def test_llm_rerank_offline_preserves_order_and_topk():
    ev = _evidence(5)
    out = rag.llm_rerank("zinc phosphate", ev, k=3)
    assert out == ev[:3]  # upstream order preserved when no LLM


def test_llm_rerank_handles_empty_and_singleton():
    assert rag.llm_rerank("x", [], k=3) == []
    one = _evidence(1)
    assert rag.llm_rerank("x", one, k=3) == one


# ── KnowledgeCohort offline integrity ────────────────────────────────────────

def test_cohort_run_offline_grounded_in_seed_corpus():
    req = Requirement(**_REQUIREMENT)
    report = knowledge_cohort.KnowledgeCohort().run(
        "epoxy zinc phosphate anti-corrosion coating", req=req
    )
    assert report.topic
    assert report.report_markdown  # non-empty even offline
    assert isinstance(report.citations, list)
    assert report.citations, "offline report must still cite seed-corpus evidence"
    assert report.engine in ("llm", "offline")
    # Candidate formulations are produced from the requirement.
    assert report.candidates


def test_cohort_run_without_requirement():
    report = knowledge_cohort.conduct_research("anti-corrosion coating")
    assert report.report_markdown
    assert report.candidates == []  # no requirement → no candidates


# ── Deep research async (202 + SSE) ──────────────────────────────────────────

def test_deep_research_returns_202_and_completes():
    body = {
        "topic": "low-temperature curing anti-corrosion primer",
        "requirement": _REQUIREMENT,
        "sources": [],
        "query": "low-temperature curing anti-corrosion primer",
    }
    r = client.post("/api/research/deep", json=body)
    assert r.status_code == 202
    handle = r.json()
    assert handle["stream_url"].endswith("/stream")
    assert handle["status_url"].endswith(handle["task_id"])

    st = None
    for _ in range(240):
        st = client.get(handle["status_url"]).json()
        if st["state"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert st["state"] == "completed", st
    assert st["result"]["report"]["report_markdown"]
