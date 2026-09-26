"""P2: cover /api/session/* HTTP routes (save/load/info/list/delete)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Point session memory at an isolated SQLite file."""
    from app.api import session as session_api
    from app.db.database import make_engine, make_session_factory
    from app.services.session.memory_service import SessionMemoryService

    engine = make_engine(f"sqlite:///{(tmp_path / 'sess-api.db').as_posix()}")
    factory = make_session_factory(engine)
    service = SessionMemoryService.__new__(SessionMemoryService)
    service._session_factory = factory
    service._initialized = True
    service._redis = None

    async def _avail():
        return True

    async def _init():
        return True

    service.is_available = _avail  # type: ignore[method-assign]
    service.initialize = _init  # type: ignore[method-assign]

    # Depends() resolves the symbol imported into the API module.
    monkeypatch.setattr(session_api, "get_session_memory_service", lambda: service)
    with TestClient(app) as c:
        yield c
    engine.dispose()


def test_session_save_load_info_list_delete(client):
    sid = "p2-session-1"
    payload = {
        "session_id": sid,
        "history": [
            {"role": "user", "content": "耐盐雾怎么提高?"},
            {"role": "assistant", "content": "硅烷钝化…"},
        ],
        "project_id": "proj-p2",
        "title": "P2 session",
        "ttl_seconds": 3600,
    }
    r = client.post("/api/session/save", json=payload)
    assert r.status_code == 200
    assert r.json().get("status") == "success"

    r = client.get(f"/api/session/load/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["history"]) == 2
    assert body.get("project_id") == "proj-p2"
    assert body.get("title") == "P2 session"

    r = client.get(f"/api/session/info/{sid}")
    assert r.status_code == 200
    info = r.json()
    assert info["session_id"] == sid
    assert info["history_count"] >= 1

    r = client.get("/api/session/list", params={"project_id": "proj-p2"})
    assert r.status_code == 200
    listed = r.json()
    assert listed["total_count"] >= 1
    assert any(s["session_id"] == sid for s in listed["sessions"])

    r = client.delete(f"/api/session/delete/{sid}")
    assert r.status_code == 200
    assert r.json().get("success") is True

    r = client.get(f"/api/session/load/{sid}")
    assert r.status_code == 404


def test_session_load_missing_404(client):
    r = client.get("/api/session/load/does-not-exist")
    assert r.status_code == 404
