"""Publication-preflight HTTP surface, incl. the new ``/preflight/resolve``.

The service layer (``publication_preflight``) was well covered, but its routes
had no tests at all and ``resolve_finding`` had no route: a blocked export could
be overridden over HTTP yet never marked as genuinely fixed.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import publication_preflight as pf

PID = "p-preflight"
MD = "Intro text with [citation needed] and a claim [^7].\n\n[^1]: Source A\n"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setattr(pf, "_data_root", lambda: tmp_path)
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def _review(client, kind="storm", markdown=MD):
    res = client.post(
        "/api/wiki/preflight/review", json={"project_id": PID, "kind": kind, "markdown": markdown}
    )
    assert res.status_code == 200, res.text
    return res.json()


def _blocking(state):
    return [f for f in state["findings"] if f["severity"] == "blocking" and f["status"] == "open"]


def test_review_then_get_returns_the_same_state(client):
    state = _review(client)
    assert state["open_blocking"] >= 1 and state["ready"] is False

    got = client.get(f"/api/wiki/preflight/{PID}", params={"kind": "storm"}).json()
    assert got["content_hash"] == state["content_hash"]
    assert {f["id"] for f in got["findings"]} == {f["id"] for f in state["findings"]}


def test_state_is_kept_per_kind(client):
    _review(client, kind="storm")
    other = client.get(f"/api/wiki/preflight/{PID}", params={"kind": "tech_report_doe"}).json()
    assert other["findings"] == [] and other["open_blocking"] == 0


def test_resolve_marks_a_finding_handled_and_requires_a_note_for_blocking(client):
    fid = _blocking(_review(client))[0]["id"]

    empty = client.post(
        "/api/wiki/preflight/resolve",
        json={"project_id": PID, "kind": "storm", "finding_id": fid, "actor": "alice", "note": ""},
    )
    assert empty.status_code == 400, empty.text

    ok = client.post(
        "/api/wiki/preflight/resolve",
        json={"project_id": PID, "kind": "storm", "finding_id": fid, "actor": "alice",
              "note": "placeholder replaced in the source section"},
    )
    assert ok.status_code == 200, ok.text
    hit = next(f for f in ok.json()["findings"] if f["id"] == fid)
    assert hit["status"] == "resolved"
    assert hit["resolution"]["actor"] == "alice"
    assert hit["resolution"]["note"] == "placeholder replaced in the source section"


def test_resolve_unknown_finding_is_404_and_missing_actor_is_422(client):
    _review(client)
    assert client.post(
        "/api/wiki/preflight/resolve",
        json={"project_id": PID, "finding_id": "nope", "actor": "a", "note": "n"},
    ).status_code == 404
    assert client.post(
        "/api/wiki/preflight/resolve", json={"project_id": PID, "finding_id": "x", "note": "n"}
    ).status_code == 422


def test_resolving_an_already_handled_finding_is_rejected(client):
    fid = _blocking(_review(client))[0]["id"]
    body = {"project_id": PID, "finding_id": fid, "actor": "a", "note": "done"}
    assert client.post("/api/wiki/preflight/resolve", json=body).status_code == 200
    assert client.post("/api/wiki/preflight/resolve", json=body).status_code == 400


def test_override_needs_actor_and_reason_and_leaves_an_audit_trail(client):
    fid = _blocking(_review(client))[0]["id"]
    blank = client.post(
        "/api/wiki/preflight/override",
        json={"project_id": PID, "finding_id": fid, "actor": "  ", "reason": "x"},
    )
    assert blank.status_code == 400

    ok = client.post(
        "/api/wiki/preflight/override",
        json={"project_id": PID, "finding_id": fid, "actor": "bob", "reason": "draft only"},
    )
    assert ok.status_code == 200, ok.text
    hit = next(f for f in ok.json()["findings"] if f["id"] == fid)
    assert hit["status"] == "overridden" and hit["resolution"]["reason"] == "draft only"
    assert any(e.get("type") == "overridden" and e.get("actor") == "bob" for e in ok.json()["events"])


def test_finalize_is_refused_until_every_blocking_finding_is_handled(client):
    state = _review(client)
    refused = client.post(
        "/api/wiki/preflight/finalize", json={"project_id": PID, "markdown": MD, "actor": "alice"}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["ready"] is False
    assert refused.json()["detail"]["errors"], "the UI shows these reasons"

    for f in _blocking(state):
        r = client.post(
            "/api/wiki/preflight/resolve",
            json={"project_id": PID, "finding_id": f["id"], "actor": "alice", "note": "fixed"},
        )
        assert r.status_code == 200, r.text

    done = client.post(
        "/api/wiki/preflight/finalize", json={"project_id": PID, "markdown": MD, "actor": "alice"}
    )
    assert done.status_code == 200, done.text
    assert done.json()["ready"] is True and done.json()["state"]["ready"] is True
    assert done.json()["state"]["finalization"]["actor"] == "alice"


def test_export_gate_clears_after_resolve(client, monkeypatch):
    """The user-visible loop: blocked export → resolve → export allowed."""
    settings = get_settings()
    allowed, _ = pf.export_allowed(PID, "storm", MD, settings=settings)
    assert allowed is False

    for f in _blocking(pf.get_state(PID, "storm")):
        pf.resolve_finding(PID, "storm", f["id"], actor="alice", note="fixed")

    allowed, detail = pf.export_allowed(PID, "storm", MD, settings=settings)
    assert allowed is True and detail["ready"] is True
