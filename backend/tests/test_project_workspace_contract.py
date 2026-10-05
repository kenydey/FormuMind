"""What the UI saves into a project, the backend must give back (round-4).

``PUT /api/projects/{id}`` merges the request into the stored payload and then runs it through
``ProjectWorkspace.model_validate`` — so every key the model does not declare is dropped on the floor,
with no error. The frontend sent ``auto_loop_max_rounds`` / ``auto_loop_round`` all along: after a reload
the user's "at most N rounds" setting was back at 5, and the round counter that bounds unattended
auto-iterations restarted at 0. ``Formulation.client_uid`` (the DOE-baseline badge identity) was dropped
the same way.

The first test is the scan, kept: any key ``buildWorkspacePayload`` sends that the model lacks fails here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.domain.project_workspace import ProjectWorkspace
from app.domain.schemas import Formulation
from app.main import app

TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "projectWorkspace.ts"


def _payload_keys() -> list[str]:
    text = TS.read_text(encoding="utf-8")
    body = text.split("export interface ProjectWorkspacePayload {", 1)[1].split("\n}", 1)[0]
    return re.findall(r"^\s{2}([a-z_]+)\??:", body, flags=re.MULTILINE)


@pytest.mark.skipif(not TS.is_file(), reason="frontend sources not present")
def test_every_key_the_frontend_saves_is_a_field_of_the_workspace_model():
    keys = _payload_keys()
    assert len(keys) > 30, "the interface reader found too little to mean anything"
    dropped = [k for k in keys if k not in ProjectWorkspace.model_fields]
    assert not dropped, f"saved by the UI but silently dropped by the backend: {dropped}"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/ws.db")
    Base.metadata.create_all(engine)
    import app.db.project_store as ps_mod

    monkeypatch.setattr(ps_mod, "_store", ProjectStore(make_session_factory(engine)))
    yield TestClient(app)
    get_settings.cache_clear()


def test_the_auto_loop_cap_and_counter_survive_a_reload(client):
    project = client.post("/api/projects", json={"title": "loop"}).json()
    r = client.put(
        f"/api/projects/{project['id']}",
        json={"workspace": {"auto_loop_on_sync": True, "auto_loop_max_rounds": 9, "auto_loop_round": 3}},
    )
    assert r.status_code == 200, r.text
    workspace = client.get(f"/api/projects/{project['id']}").json()["workspace"]
    assert (workspace["auto_loop_max_rounds"], workspace["auto_loop_round"]) == (9, 3)


def test_a_project_saved_before_these_fields_existed_gets_the_old_defaults():
    ws = ProjectWorkspace.model_validate({"search_query": "legacy"})
    assert (ws.auto_loop_max_rounds, ws.auto_loop_round) == (5, 0)


def test_the_baseline_badge_identity_survives_a_reload(client):
    project = client.post("/api/projects", json={"title": "baseline"}).json()
    card = {
        "name": "Zn primer", "domain": "anticorrosion_coating", "ingredients": [{"name": "Zinc phosphate", "role": "inhibitor", "weight_pct": 5.0}],
        "client_uid": "uid-123",
    }
    r = client.put(f"/api/projects/{project['id']}", json={"workspace": {"leaderboard": [card], "requirement": None}})
    assert r.status_code == 200, r.text
    again = client.get(f"/api/projects/{project['id']}").json()["workspace"]
    assert again["leaderboard"][0]["client_uid"] == "uid-123"
    assert Formulation.model_validate(card).client_uid == "uid-123"
