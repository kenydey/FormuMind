"""Tests for W2-5 (P1-15) Session Plan service + API."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import session_plans as plans_api
from app.services import session_plan as svc


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def api_client(data_dir):
    app = FastAPI()
    app.include_router(plans_api.router)
    return TestClient(app)


def _phases():
    return [
        {"name": "design", "steps": ["define factors", "choose design"]},
        {"name": "execute", "steps": ["run batch 1"]},
        {"name": "optimize", "steps": ["fit surrogate", "propose next"]},
    ]


# ---------- service: submit / get ----------


def test_submit_plan_pending(data_dir):
    plan = svc.submit_plan("sess-1", _phases())
    assert plan.status == "pending"
    assert plan.session_id == "sess-1"
    assert [p.name for p in plan.phases] == ["design", "execute", "optimize"]
    assert all(s.status == "pending" for p in plan.phases for s in p.steps)
    path = data_dir / "session_plans" / f"{plan.plan_id}.json"
    assert path.is_file()


def test_submit_plan_validation(data_dir):
    with pytest.raises(ValueError):
        svc.submit_plan("s", [])
    with pytest.raises(ValueError):
        svc.submit_plan("s", [{"name": "x", "steps": []}])
    with pytest.raises(ValueError):
        svc.submit_plan("s", [{"name": "x", "steps": ["  "]}])
    with pytest.raises(ValueError):
        svc.submit_plan("  ", _phases())


def test_get_plan_missing(data_dir):
    assert svc.get_plan("nope") is None


# ---------- service: decide ----------


def test_decide_approve(data_dir):
    plan = svc.submit_plan("s", _phases())
    decided = svc.decide_plan(plan.plan_id, True, actor="cheng")
    assert decided.status == "approved"
    assert decided.decided_by == "cheng"
    assert decided.decided_at is not None


def test_decide_reject(data_dir):
    plan = svc.submit_plan("s", _phases())
    decided = svc.decide_plan(plan.plan_id, False)
    assert decided.status == "rejected"


def test_decide_irreversible(data_dir):
    plan = svc.submit_plan("s", _phases())
    svc.decide_plan(plan.plan_id, True)
    with pytest.raises(ValueError):
        svc.decide_plan(plan.plan_id, False)
    with pytest.raises(ValueError):
        svc.decide_plan(plan.plan_id, True)


def test_decide_missing(data_dir):
    with pytest.raises(KeyError):
        svc.decide_plan("nope", True)


# ---------- service: advance ----------


def test_advance_requires_approval(data_dir):
    plan = svc.submit_plan("s", _phases())
    with pytest.raises(ValueError, match="not approved"):
        svc.advance(plan.plan_id, "design", 0)


def test_advance_phase_gate(data_dir):
    plan = svc.submit_plan("s", _phases())
    svc.decide_plan(plan.plan_id, True)
    # phase 2 blocked while phase 1 incomplete
    with pytest.raises(ValueError, match="blocked"):
        svc.advance(plan.plan_id, "execute", 0)
    # complete phase 1, then phase 2 opens
    svc.advance(plan.plan_id, "design", 0)
    svc.advance(plan.plan_id, "design", 1)
    out = svc.advance(plan.plan_id, "execute", 0)
    assert out.phases[1].steps[0].status == "done"
    # phase 3 still blocked (phase 2 has 1 step, now done -> opens)
    out = svc.advance(plan.plan_id, "optimize", 0)
    assert out.phases[2].steps[0].status == "done"


def test_advance_idempotent(data_dir):
    plan = svc.submit_plan("s", _phases())
    svc.decide_plan(plan.plan_id, True)
    svc.advance(plan.plan_id, "design", 0)
    again = svc.advance(plan.plan_id, "design", 0)  # no error
    assert again.phases[0].steps[0].status == "done"


def test_advance_bad_inputs(data_dir):
    plan = svc.submit_plan("s", _phases())
    svc.decide_plan(plan.plan_id, True)
    with pytest.raises(ValueError):
        svc.advance(plan.plan_id, "no-such-phase", 0)
    with pytest.raises(ValueError):
        svc.advance(plan.plan_id, "design", 99)
    with pytest.raises(KeyError):
        svc.advance("nope", "design", 0)


# ---------- service: sha256 persistence ----------


def test_sha256_on_disk_and_tamper(data_dir):
    plan = svc.submit_plan("s", _phases())
    path = data_dir / "session_plans" / f"{plan.plan_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "sha256" in raw and len(raw["sha256"]) == 64
    # untampered reload works
    assert svc.get_plan(plan.plan_id).plan_id == plan.plan_id
    # tamper -> checksum mismatch
    raw["status"] = "approved"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        svc.get_plan(plan.plan_id)


# ---------- API ----------


def test_api_full_flow(api_client):
    r = api_client.post("/api/session-plans", json={"session_id": "s1", "phases": _phases()})
    assert r.status_code == 200
    pid = r.json()["plan_id"]
    assert r.json()["status"] == "pending"

    r = api_client.get(f"/api/session-plans/{pid}")
    assert r.status_code == 200
    assert r.json()["session_id"] == "s1"

    # advance before approval -> 409
    r = api_client.post(f"/api/session-plans/{pid}/advance",
                        json={"phase_name": "design", "step_idx": 0})
    assert r.status_code == 409

    r = api_client.post(f"/api/session-plans/{pid}/decide",
                        json={"approved": True, "actor": "cheng"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # phase gate -> 409
    r = api_client.post(f"/api/session-plans/{pid}/advance",
                        json={"phase_name": "execute", "step_idx": 0})
    assert r.status_code == 409

    r = api_client.post(f"/api/session-plans/{pid}/advance",
                        json={"phase_name": "design", "step_idx": 0})
    assert r.status_code == 200
    assert r.json()["phases"][0]["steps"][0]["status"] == "done"


def test_api_404_and_409(api_client):
    assert api_client.get("/api/session-plans/nope").status_code == 404
    r = api_client.post("/api/session-plans/nope/decide", json={"approved": True})
    assert r.status_code == 404

    r = api_client.post("/api/session-plans", json={"session_id": "s2", "phases": _phases()})
    pid = r.json()["plan_id"]
    assert api_client.post(f"/api/session-plans/{pid}/decide",
                           json={"approved": True}).status_code == 200
    # second decide -> 409 (irreversible)
    r = api_client.post(f"/api/session-plans/{pid}/decide", json={"approved": False})
    assert r.status_code == 409

    # invalid body -> 422
    r = api_client.post("/api/session-plans", json={"session_id": "s3", "phases": []})
    assert r.status_code == 422
