"""KG P0 — chat API with entity resolution opt-in."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.kg_schemas import KGRetrieveStats
from app.domain.schemas import Evidence


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


def test_chat_kg_path_merges_evidence(monkeypatch):
    kb_hit = Evidence(
        source="patent",
        identifier="kb:s#c0",
        title="含锌专利",
        snippet="磷酸锌 fifteen parts",
        relevance=0.9,
        entity_refs=[
            {
                "entity_id": "chem:cas:7779-90-0",
                "kind": "chemical",
                "display_name": "7779-90-0",
                "composition_status": "resolved",
            }
        ],
    )

    class FakeResult:
        evidence = [kb_hit]
        stats = KGRetrieveStats(chunks_sent_to_llm=1)

    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.kg.retrieve", lambda *a, **k: FakeResult())

    resp = _client().post(
        "/api/chat",
        json={
            "question": "磷酸锌用量？",
            "sources": [],
            "include_entity_resolution": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kb_chunks_used"] >= 1
    assert data["entity_resolution"] is not None
    assert any(c["identifier"] == "kb:s#c0" for c in data["citations"])
