"""D 检索冷启动预热：prewarm 状态透传与幂等."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_rag_status_includes_prewarm():
    client = TestClient(app)
    r = client.get("/api/research/rag/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "prewarm" in body
    assert "status" in body["prewarm"]


def test_prewarm_idempotent():
    client = TestClient(app)
    r1 = client.post("/api/research/rag/prewarm?background=false")
    assert r1.status_code == 200, r1.text
    s1 = r1.json()
    assert s1["status"] in ("ready", "failed", "warming")
    r2 = client.post("/api/research/rag/prewarm?background=true")
    assert r2.status_code == 200
    # 幂等：第二次不应重置为 idle
    assert r2.json()["status"] in ("ready", "warming", "failed")
