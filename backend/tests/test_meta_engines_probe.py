"""GET /api/meta exposes real import probes for optional engines."""
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


def test_meta_includes_engines_probe(monkeypatch):
    import app.services.engines.status as status_mod

    monkeypatch.setattr(
        "app.services.engines.doe_registry.baybe_available", lambda: False
    )
    monkeypatch.setattr(
        "app.services.engines.doe_registry.pydoe_available", lambda: True
    )
    monkeypatch.setattr(status_mod, "_probe", lambda name: name == "optuna")
    monkeypatch.setattr("app.services.rag._embedding_available", lambda: False)

    with TestClient(app) as client:
        r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    engines = body["engines"]
    assert set(engines) >= {
        "baybe",
        "pydoe",
        "optuna",
        "botorch",
        "summit",
        "sentence_transformers",
        "docling",
    }
    assert engines["baybe"]["available"] is False
    assert engines["pydoe"]["available"] is True
    assert engines["optuna"]["available"] is True
    assert engines["botorch"]["available"] is False
    assert engines["sentence_transformers"]["available"] is False
    assert "label" in engines["baybe"]
