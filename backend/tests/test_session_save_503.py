"""POST /api/session/save: a service that cannot persist must answer 503.

``save_session`` raised ``HTTPException(503)`` inside a ``try`` whose broad
``except Exception`` then converted it into a 500 ("Internal server error: 503:
Failed to save session…"). ``load_session`` and the other routes already
re-raise HTTPException first; this one did not.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


class _Service:
    def __init__(self, save_result=False, save_exc: Exception | None = None):
        self._save_result = save_result
        self._save_exc = save_exc

    async def is_available(self):
        return True

    async def initialize(self):
        return True

    async def save_chat_session(self, **kwargs):
        if self._save_exc is not None:
            raise self._save_exc
        return self._save_result


@pytest.fixture(autouse=True)
def _clean_overrides():
    yield
    app.dependency_overrides.clear()


def _client(monkeypatch, service) -> TestClient:
    from app.api import session as session_api

    # Depends(...) holds the original function object, so patching the module
    # attribute does not reach the route — override the dependency itself.
    app.dependency_overrides[session_api.get_session_memory_service] = lambda: service
    return TestClient(app)


PAYLOAD = {"session_id": "s-503", "history": [{"role": "user", "content": "hi"}], "ttl_seconds": 3600}


def test_unavailable_store_is_a_503_not_a_500(monkeypatch):
    res = _client(monkeypatch, _Service(save_result=False)).post("/api/session/save", json=PAYLOAD)
    assert res.status_code == 503, res.text
    assert res.json()["detail"] == "Failed to save session (service unavailable)"


def test_unexpected_errors_are_still_a_500(monkeypatch):
    res = _client(monkeypatch, _Service(save_exc=RuntimeError("disk on fire"))).post(
        "/api/session/save", json=PAYLOAD
    )
    assert res.status_code == 500
