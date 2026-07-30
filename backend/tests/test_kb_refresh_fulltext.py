"""The 「补充知识库」 button must build the store chat actually reads.

It used to stop at `colbert_store.index_evidence`, which writes `title + snippet`
clipped to 500 chars into `evidence_registry.json`. Chat retrieval goes through
`kb_index.search_chunks` against `document_chunks` and never opens that file, so
the button contributed nothing to question answering — the one thing it exists
for.

Two things are pinned here, and they pull in opposite directions:
  * the full-text pipeline is now triggered (the fix), and
  * the registry write is still made (deep research's retrieve node searches it
    at `pipeline/research_graph.py:158`, so dropping it would silently cost that
    path its recall — which is what a naive "replace" would have done).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import Evidence
from app.main import app

client = TestClient(app)


def _evidence(n: int, *, with_url: bool = True) -> list[Evidence]:
    return [
        Evidence(
            title=f"水性环氧防腐涂料 CN10{i}",
            snippet="摘要片段。" * 10,
            source="patents",
            identifier=f"CN10{i}A",
            url=f"https://patents.example.com/CN10{i}A" if with_url else "",
            relevance=0.9,
        )
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _auth_off(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stubbed(monkeypatch):
    """Federated search returns canned hits; record what each store is asked."""
    from app.api import research as research_api
    from app.services import colbert_store, kb_ingest
    from app.worker import tasks as worker_tasks

    calls: dict[str, object] = {"registry": None, "dispatched": None}

    class _Fed:
        def search(self, query, **kw):
            from app.services.federated_search import FederatedSearchResult

            return FederatedSearchResult(
                evidence=_evidence(4), source_counts={"patents": 4}
            )

    monkeypatch.setattr(research_api, "FederatedSearchEngine", _Fed)
    monkeypatch.setattr(
        colbert_store, "index_evidence",
        lambda ev, **kw: calls.__setitem__("registry", len(ev)) or len(ev),
    )
    monkeypatch.setattr(
        worker_tasks, "dispatch_kb_ingest",
        lambda dicts, **kw: calls.__setitem__("dispatched", len(dicts)) or "kbingest-abc",
    )
    monkeypatch.setattr(
        kb_ingest, "select_ingest_targets",
        lambda ev, **kw: [(e, "patent") for e in ev[:3]],
    )
    return calls


def test_refresh_triggers_the_full_text_pipeline(stubbed):
    body = client.post("/api/research/kb/refresh", params={"query": "水性环氧"}).json()

    # The fix: a real ingest job, not just a registry row.
    assert body["ingest_task_id"] == "kbingest-abc"
    assert stubbed["dispatched"] == 4


def test_refresh_still_feeds_the_registry_deep_research_reads(stubbed):
    client.post("/api/research/kb/refresh", params={"query": "水性环氧"})
    # research_graph's retrieve node searches this store. Replacing rather than
    # adding would have cost deep research its recall, silently.
    assert stubbed["registry"] == 4


def test_found_and_ingestable_are_reported_separately(stubbed):
    body = client.post("/api/research/kb/refresh", params={"query": "水性环氧"}).json()
    # "found 4, fetching 3" is honest; the gap is rows with nothing to download,
    # not data loss. Conflating them is what made the old `fetched` misleading.
    assert body["found"] == 4
    assert body["ingestable"] == 3
    assert body["source_counts"] == {"patents": 4}


def test_no_results_reports_no_task(monkeypatch):
    from app.api import research as research_api

    class _Empty:
        def search(self, query, **kw):
            from app.services.federated_search import FederatedSearchResult

            return FederatedSearchResult(evidence=[], source_counts={})

    monkeypatch.setattr(research_api, "FederatedSearchEngine", _Empty)
    body = client.post("/api/research/kb/refresh", params={"query": "无结果"}).json()
    assert body["found"] == 0
    assert body["ingest_task_id"] is None


def test_response_no_longer_claims_documents_were_indexed(stubbed):
    """`fetched`/`indexed_total` were the misleading pair: `fetched` counted
    search hits and `indexed_total` the registry's cumulative size, so the UI
    said "已入库 20 条（索引共 340）" having stored no document text at all."""
    body = client.post("/api/research/kb/refresh", params={"query": "水性环氧"}).json()
    assert "fetched" not in body
    assert "indexed_total" not in body
