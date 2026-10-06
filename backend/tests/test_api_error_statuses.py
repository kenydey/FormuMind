"""Caller mistakes and unavailable dependencies must not look like server faults (round-4).

Found by walking every OpenAPI operation with a minimal request: three of them answered 500.

* ``GET /api/examples/{id}`` with an unknown id raised ``KeyError`` out of the handler;
* ``POST /api/formulations/manual`` with no ingredients — validation drops the recipe and the handler then read
  ``forms[0]`` of an empty list (``IndexError``);
* ``POST /api/experiments/hooks/pause-doecycle/{id}`` answered 500 whenever the flag store (then Redis, now the
  database) could not be written, while the status endpoint right next to it degrades gracefully.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/err.db")
    get_settings.cache_clear()
    from app.db.database import Base, default_engine

    Base.metadata.create_all(default_engine())
    yield TestClient(app)
    get_settings.cache_clear()


def test_an_unknown_example_project_is_a_404(client):
    r = client.get("/api/examples/no-such-example")
    assert r.status_code == 404
    assert "no-such-example" in r.json()["detail"]


def test_a_known_example_still_loads(client):
    from app.domain.examples import EXAMPLE_PROJECTS

    example_id = next(iter(EXAMPLE_PROJECTS))
    assert client.get(f"/api/examples/{example_id}").status_code == 200


def test_a_manual_formulation_without_ingredients_is_a_422_not_a_500(client):
    body = {"formulation": {"name": "empty", "domain": "anticorrosion_coating", "ingredients": []}}
    r = client.post("/api/formulations/manual", json=body)
    assert r.status_code == 422, r.text
    assert "配方未通过校验" in r.json()["detail"]


def test_a_manual_formulation_with_ingredients_is_accepted(client):
    body = {
        "formulation": {
            "name": "ok", "domain": "anticorrosion_coating",
            "ingredients": [{"name": "Zinc phosphate", "role": "inhibitor", "weight_pct": 5.0}],
        }
    }
    r = client.post("/api/formulations/manual", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["formulation"]["source"] == "manual"


@pytest.fixture()
def campaign_id(client):
    from app.db.database import default_session_factory
    from app.db.models import Campaign

    with default_session_factory()() as session:
        row = Campaign(name="pause-target")
        session.add(row)
        session.commit()
        return row.id


def test_pausing_without_a_state_store_is_a_503(client, campaign_id, monkeypatch):
    import app.services.workbench_loop as loop

    monkeypatch.setattr(loop, "pause_resume_doecyle", lambda campaign_id, is_paused, **kw: False)
    r = client.post(f"/api/experiments/hooks/pause-doecycle/{campaign_id}", json={"isPaused": True})
    assert r.status_code == 503
    assert "state store unavailable" in r.json()["detail"]


def test_pausing_with_a_state_store_still_succeeds(client, campaign_id):
    r = client.post(f"/api/experiments/hooks/pause-doecycle/{campaign_id}", json={"isPaused": True})
    assert r.status_code == 200 and r.json()["status"] == "success"


def test_pausing_a_campaign_that_does_not_exist_is_a_404_not_a_phantom_flag(client):
    r = client.post("/api/experiments/hooks/pause-doecycle/3", json={"isPaused": True})
    assert r.status_code == 404
