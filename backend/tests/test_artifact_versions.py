"""Tests for W4-1 (P1-16): artifact version state machine.

Covers: illegal transitions rejected, finalized immutability, derivation
graph correctness, sha256 tamper detection, fail-open freeze hook, and the
``artifact_versions_enabled`` kill-switch (404 when off).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import artifact_versions as api_mod
from app.services import artifact_versions as svc


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def api_client(data_dir):
    app = FastAPI()
    app.include_router(api_mod.router)
    return TestClient(app)


def _lineage(api_client, name="report-a"):
    r = api_client.post(
        "/api/artifacts/lineages",
        json={"project_id": "proj-1", "name": name, "kind": "report"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _version(api_client, lineage_id, content="v1 text", based_on=None, actor="tester"):
    body = {"content": content, "actor": actor}
    if based_on:
        body["based_on_version_id"] = based_on
    r = api_client.post(f"/api/artifacts/lineages/{lineage_id}/versions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_create_lineage_and_version(api_client):
    lin = _lineage(api_client)
    assert lin["project_id"] == "proj-1"
    v = _version(api_client, lin["lineage_id"])
    assert v["status"] == "staging"
    assert v["lineage_id"] == lin["lineage_id"]
    assert v["based_on_version_id"] is None
    assert v["sha256"]  # content hash recorded
    r = api_client.get(f"/api/artifacts/versions/{v['version_id']}/verify")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_staging_content_rewritable(api_client):
    lin = _lineage(api_client)
    v = _version(api_client, lin["lineage_id"], content="draft")
    r = api_client.put(
        f"/api/artifacts/versions/{v['version_id']}/content", json={"content": "revised"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["sha256"] != v["sha256"]
    content = svc.get_version_content(v["version_id"])
    assert content == "revised".encode("utf-8")


def test_illegal_transitions_rejected(api_client):
    lin = _lineage(api_client)
    v = _version(api_client, lin["lineage_id"])
    vid = v["version_id"]
    # staging → finalized directly is illegal
    r = api_client.post(f"/api/artifacts/versions/{vid}/finalize")
    assert r.status_code == 409, r.text
    # legal path
    assert api_client.post(f"/api/artifacts/versions/{vid}/submit").status_code == 200
    # submit twice is illegal
    r = api_client.post(f"/api/artifacts/versions/{vid}/submit")
    assert r.status_code == 409, r.text
    # finalized, then finalize again is illegal
    assert api_client.post(f"/api/artifacts/versions/{vid}/finalize").status_code == 200
    r = api_client.post(f"/api/artifacts/versions/{vid}/finalize")
    assert r.status_code == 409, r.text
    # finalized → submit is illegal (terminal)
    r = api_client.post(f"/api/artifacts/versions/{vid}/submit")
    assert r.status_code == 409, r.text


def test_finalized_immutable(api_client):
    lin = _lineage(api_client)
    v = _version(api_client, lin["lineage_id"], content="final text")
    vid = v["version_id"]
    api_client.post(f"/api/artifacts/versions/{vid}/submit")
    api_client.post(f"/api/artifacts/versions/{vid}/finalize")
    r = api_client.put(f"/api/artifacts/versions/{vid}/content", json={"content": "sneaky"})
    assert r.status_code == 409, r.text
    assert "immutable" in r.json()["detail"]
    assert svc.get_version_content(vid) == "final text".encode("utf-8")


def test_derivation_graph(api_client):
    lin = _lineage(api_client)
    lid = lin["lineage_id"]
    v1 = _version(api_client, lid, content="v1")
    v2 = _version(api_client, lid, content="v2", based_on=v1["version_id"])
    v3 = _version(api_client, lid, content="v3-branch", based_on=v1["version_id"])
    assert v2["based_on_version_id"] == v1["version_id"]
    assert v3["based_on_version_id"] == v1["version_id"]
    r = api_client.get(f"/api/artifacts/lineages/{lid}/versions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["versions"]) == 3
    edges = {e["version_id"]: e["based_on_version_id"] for e in body["graph"]}
    assert edges[v1["version_id"]] is None
    assert edges[v2["version_id"]] == v1["version_id"]
    assert edges[v3["version_id"]] == v1["version_id"]
    # based_on must belong to the same lineage
    other = _lineage(api_client, name="other")
    r = api_client.post(
        f"/api/artifacts/lineages/{other['lineage_id']}/versions",
        json={"content": "x", "based_on_version_id": v1["version_id"]},
    )
    assert r.status_code == 400, r.text


def test_sha256_tamper_detected(api_client, data_dir):
    lin = _lineage(api_client)
    v = _version(api_client, lin["lineage_id"], content="pristine")
    vid = v["version_id"]
    assert svc.verify_version(vid)["ok"] is True
    # tamper with the snapshot on disk
    (data_dir / "artifacts" / "versions" / vid / "content.bin").write_bytes(b"tampered!")
    result = svc.verify_version(vid)
    assert result["ok"] is False
    assert result["stored_sha256"] != result["actual_sha256"]
    r = api_client.get(f"/api/artifacts/versions/{vid}/verify")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False


def test_freeze_evidence_fail_open(api_client, monkeypatch):
    # W4-2 hook failure must never block finalize.
    def boom(version_id, version_dict):
        raise RuntimeError("freeze exploded")

    monkeypatch.setattr(svc, "freeze_evidence", boom)
    lin = _lineage(api_client)
    v = _version(api_client, lin["lineage_id"])
    vid = v["version_id"]
    api_client.post(f"/api/artifacts/versions/{vid}/submit")
    r = api_client.post(f"/api/artifacts/versions/{vid}/finalize")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "finalized"


def test_flag_disabled_returns_404(api_client, monkeypatch):
    monkeypatch.setattr(
        api_mod, "get_settings", lambda: SimpleNamespace(artifact_versions_enabled=False)
    )
    assert api_client.post("/api/artifacts/lineages", json={"project_id": "p", "name": "n"}).status_code == 404
    assert api_client.post("/api/artifacts/lineages/x/versions", json={}).status_code == 404
    assert api_client.get("/api/artifacts/lineages/x/versions").status_code == 404
    assert api_client.get("/api/artifacts/versions/x").status_code == 404
    assert api_client.get("/api/artifacts/versions/x/verify").status_code == 404
    assert api_client.put("/api/artifacts/versions/x/content", json={"content": "c"}).status_code == 404
    assert api_client.post("/api/artifacts/versions/x/submit").status_code == 404
    assert api_client.post("/api/artifacts/versions/x/finalize").status_code == 404


def test_unknown_ids_404(api_client):
    assert api_client.get("/api/artifacts/lineages/nope/versions").status_code == 404
    assert api_client.get("/api/artifacts/versions/nope").status_code == 404
    assert api_client.post("/api/artifacts/versions/nope/submit").status_code == 404
