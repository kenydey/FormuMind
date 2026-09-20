"""GET /api/kb/retrieval-settings — shared probe ↔ recommend knobs."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_retrieval_settings_exposes_shared_alpha(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KB_HYBRID_ALPHA", "0.47")
    monkeypatch.setenv("FORMUMIND_KB_RECOMMEND_USE_HYBRID", "true")
    monkeypatch.setenv("FORMUMIND_KB_RECOMMEND_RERANK_ENABLED", "false")
    get_settings.cache_clear()
    body = TestClient(app).get("/api/kb/retrieval-settings").json()
    assert body["kb_hybrid_alpha"] == pytest.approx(0.47)
    assert body["kb_recommend_use_hybrid"] is True
    assert body["kb_recommend_rerank_enabled"] is False
    assert "kb_recommend_top_k" in body
    assert "kb_recommend_include_global" in body
