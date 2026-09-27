"""Tests for W4-4 (P1-37): version rollback as copy-on-write restore.

Covers: restore copies content exactly, the new version's
``based_on_version_id`` points at the old version (derivation graph shows the
rollback branch), the new version starts in ``staging``, and the old
(finalized) version is untouched.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import artifact_versions as api_mod
from app.services import artifact_versions as svc


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    app = FastAPI()
    app.include_router(api_mod.router)
    return TestClient(app)


def _lineage(client, name="report-r"):
    r = client.post(
        "/api/artifacts/lineages",
        json={"project_id": "proj-r", "name": name, "kind": "report"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _version(client, lineage_id, content="v1 text", based_on=None):
    body = {"content": content, "actor": "tester"}
    if based_on:
        body["based_on_version_id"] = based_on
    r = client.post(f"/api/artifacts/lineages/{lineage_id}/versions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _finalize(client, version_id):
    r = client.post(f"/api/artifacts/versions/{version_id}/submit")
    assert r.status_code == 200, r.text
    r = client.post(f"/api/artifacts/versions/{version_id}/finalize")
    assert r.status_code == 200, r.text
    return r.json()


def test_restore_copies_content_and_starts_staging(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    lin = _lineage(client)
    v1 = _version(client, lin["lineage_id"], content="配方 v1：环氧 60%，固化剂 40%")

    r = client.post(f"/api/artifacts/versions/{v1['version_id']}/restore",
                    json={"actor": "cheng"})
    assert r.status_code == 200, r.text
    new = r.json()

    assert new["version_id"] != v1["version_id"]
    assert new["status"] == "staging"
    assert new["based_on_version_id"] == v1["version_id"]
    assert new["lineage_id"] == v1["lineage_id"]
    assert new["actor"] == "cheng"
    # copy-on-write: identical content hash
    assert new["sha256"] == v1["sha256"]
    assert new["content_bytes"] == v1["content_bytes"]
    assert svc.get_version_content(new["version_id"]) == \
        svc.get_version_content(v1["version_id"])


def test_restore_shows_in_derivation_graph(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    lin = _lineage(client)
    v1 = _version(client, lin["lineage_id"], content="a")
    v2 = _version(client, lin["lineage_id"], content="b", based_on=v1["version_id"])

    r = client.post(f"/api/artifacts/versions/{v2['version_id']}/restore", json={})
    assert r.status_code == 200, r.text
    new_id = r.json()["version_id"]

    r = client.get(f"/api/artifacts/lineages/{lin['lineage_id']}/versions")
    assert r.status_code == 200, r.text
    payload = r.json()
    edges = {e["version_id"]: e["based_on_version_id"] for e in payload["graph"]}
    assert edges[v1["version_id"]] is None
    assert edges[v2["version_id"]] == v1["version_id"]
    # rollback branch visible: new version derives from v2
    assert edges[new_id] == v2["version_id"]
    ids = {v["version_id"] for v in payload["versions"]}
    assert {v1["version_id"], v2["version_id"], new_id} <= ids


def test_restore_leaves_old_finalized_version_untouched(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    lin = _lineage(client)
    v1 = _version(client, lin["lineage_id"], content="final content")
    old_hash = v1["sha256"]
    fin = _finalize(client, v1["version_id"])
    assert fin["status"] == "finalized"

    r = client.post(f"/api/artifacts/versions/{v1['version_id']}/restore", json={})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["status"] == "staging"

    # old version untouched: still finalized, same content hash
    r = client.get(f"/api/artifacts/versions/{v1['version_id']}")
    assert r.status_code == 200, r.text
    old = r.json()
    assert old["status"] == "finalized"
    assert old["sha256"] == old_hash
    assert old["based_on_version_id"] is None  # no rewrite of history


def test_restore_missing_version_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/artifacts/versions/does-not-exist/restore", json={})
    assert r.status_code == 404


def test_restore_disabled_flag_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    lin = _lineage(client)
    v1 = _version(client, lin["lineage_id"])
    monkeypatch.setattr(svc, "artifact_versions_enabled", lambda settings: False)
    r = client.post(f"/api/artifacts/versions/{v1['version_id']}/restore", json={})
    assert r.status_code == 404
