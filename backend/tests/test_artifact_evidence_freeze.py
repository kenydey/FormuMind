"""Tests for W4-2 (P1-4): version-level evidence freeze.

Covers: finalize auto-freezes manifest_snapshot / evidence_json /
execution_snapshot_json; evidence checksum tamper detection via verify;
per-version evidence isolation; fail-open finalize when freeze raises;
404/409 error mapping on the evidence endpoints.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import artifact_versions as api_mod
from app.services import artifact_versions as svc
from app.services import literature_manifest as lm
from app.services import provenance as prov


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    # Hermetic provenance: never touch the real DB in these tests.
    monkeypatch.setattr(prov, "lineage", lambda *a, **k: [])
    return tmp_path


@pytest.fixture()
def api_client(data_dir):
    app = FastAPI()
    app.include_router(api_mod.router)
    return TestClient(app)


def _seed_manifest(project_id: str, items: list[dict]) -> None:
    man = lm.empty_manifest(project_id)
    man["items"] = items
    lm.save_manifest(man)
    lm.freeze(project_id, item_ids=[i["id"] for i in items], actor="tester")


def _finalize(api_client, project_id: str = "proj-1", content: str = "report v1"):
    r = api_client.post(
        "/api/artifacts/lineages",
        json={"project_id": project_id, "name": "report", "kind": "report"},
    )
    assert r.status_code == 200, r.text
    lineage_id = r.json()["lineage_id"]
    r = api_client.post(
        f"/api/artifacts/lineages/{lineage_id}/versions",
        json={"content": content, "actor": "tester"},
    )
    assert r.status_code == 200, r.text
    version_id = r.json()["version_id"]
    r = api_client.post(f"/api/artifacts/versions/{version_id}/submit")
    assert r.status_code == 200, r.text
    r = api_client.post(f"/api/artifacts/versions/{version_id}/finalize")
    assert r.status_code == 200, r.text
    return r.json()


def test_finalize_freezes_all_three_payloads(api_client):
    _seed_manifest(
        "proj-1",
        [
            {"id": "a", "title": "Epoxy coating", "doi": "10.1/aaa",
             "screening": "match", "locator": {"page": 3, "figure": "2"}},
            {"id": "b", "title": "Primer", "doi": "10.1/bbb", "screening": "match"},
        ],
    )
    fin = _finalize(api_client)
    assert fin["evidence_frozen"] is True
    vid = fin["version_id"]

    r = api_client.get(f"/api/artifacts/versions/{vid}/evidence")
    assert r.status_code == 200, r.text
    ev = r.json()

    # manifest_snapshot reuses Wave B checksum logic
    ms = ev["manifest_snapshot"]
    assert ms["checksum"] == lm.content_hash_manifest(lm.load_manifest("proj-1"))
    assert len(ms["manifest_json"]["items"]) == 2

    # evidence_json: frozen items with CitationLocator-schema locators
    sources = ev["evidence_json"]["sources"]
    assert len(sources) == 2
    by_id = {s["item_id"]: s for s in sources}
    assert by_id["a"]["locator"] == {"page": 3, "figure": "2"}
    assert by_id["a"]["doi"] == "10.1/aaa"
    assert by_id["b"]["locator"] is None
    assert ev["evidence_json"]["claims"] == []  # no provenance edges seeded

    # execution_snapshot_json in write_output_receipt format
    ex = ev["execution_snapshot_json"]
    assert ex["run_kind"] == "artifact_finalize"
    assert ex["run_id"] == vid
    assert ex["input_hash"]
    assert "version_id" in ex["inputs_summary"]
    assert ex["environment"]["python"]
    assert ev["evidence_checksum"]


def test_verify_ok_and_tamper_detected(api_client, data_dir):
    _seed_manifest(
        "proj-1",
        [{"id": "a", "title": "Epoxy", "doi": "10.1/aaa", "screening": "match"}],
    )
    fin = _finalize(api_client)
    vid = fin["version_id"]

    r = api_client.post(f"/api/artifacts/versions/{vid}/evidence/verify")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["stored_evidence_checksum"] == body["actual_evidence_checksum"]
    assert body["manifest_unchanged"] is True

    # Tamper with the frozen record on disk.
    path = data_dir / "artifacts" / "versions" / vid / "evidence.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["evidence_json"]["sources"].append({"item_id": "evil"})
    path.write_text(json.dumps(rec), encoding="utf-8")

    r = api_client.post(f"/api/artifacts/versions/{vid}/evidence/verify")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False


def test_evidence_isolated_per_version(api_client):
    _seed_manifest(
        "proj-1",
        [{"id": "a", "title": "Epoxy", "doi": "10.1/aaa", "screening": "match"}],
    )
    v1 = _finalize(api_client, content="v1")

    # Evolve the live manifest, then freeze a second version on a new lineage.
    man = lm.load_manifest("proj-1")
    man["items"].append(
        {"id": "c", "title": "Topcoat", "doi": "10.1/ccc", "screening": "match"}
    )
    lm.save_manifest(man)
    lm.freeze("proj-1", item_ids=["a", "c"], actor="tester")
    v2 = _finalize(api_client, content="v2")

    e1 = api_client.get(f"/api/artifacts/versions/{v1['version_id']}/evidence").json()
    e2 = api_client.get(f"/api/artifacts/versions/{v2['version_id']}/evidence").json()
    assert {s["item_id"] for s in e1["evidence_json"]["sources"]} == {"a"}
    assert {s["item_id"] for s in e2["evidence_json"]["sources"]} == {"a", "c"}
    assert (
        e1["manifest_snapshot"]["checksum"]
        != e2["manifest_snapshot"]["checksum"]
    )
    # v1's snapshot no longer matches the live (evolved) manifest — informational.
    v = api_client.post(
        f"/api/artifacts/versions/{v1['version_id']}/evidence/verify"
    ).json()
    assert v["ok"] is True  # frozen record itself intact
    assert v["manifest_unchanged"] is False


def test_claims_captured_from_provenance(api_client, monkeypatch):
    _seed_manifest(
        "proj-1",
        [{"id": "a", "title": "Epoxy", "doi": "10.1/aaa", "screening": "match"}],
    )
    monkeypatch.setattr(
        prov,
        "lineage",
        lambda *a, **k: [
            {"from_type": "claim", "from_id": "claim:abc",
             "to_type": "source", "to_id": "a", "relation": "cites"},
            {"from_type": "claim", "from_id": "claim:abc",
             "to_type": "source", "to_id": "b", "relation": "cites"},
        ],
    )
    fin = _finalize(api_client)
    ev = api_client.get(
        f"/api/artifacts/versions/{fin['version_id']}/evidence"
    ).json()
    assert ev["evidence_json"]["claims"] == [
        {"claim_id": "claim:abc", "cites": ["a", "b"]}
    ]


def test_freeze_failure_is_fail_open(api_client, monkeypatch):
    def _boom(version_id, version_dict):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "freeze_evidence", _boom)
    r = api_client.post(
        "/api/artifacts/lineages",
        json={"project_id": "proj-1", "name": "r", "kind": "report"},
    )
    lineage_id = r.json()["lineage_id"]
    r = api_client.post(
        f"/api/artifacts/lineages/{lineage_id}/versions",
        json={"content": "x", "actor": "tester"},
    )
    vid = r.json()["version_id"]
    api_client.post(f"/api/artifacts/versions/{vid}/submit")
    r = api_client.post(f"/api/artifacts/versions/{vid}/finalize")
    assert r.status_code == 200, r.text  # finalize still succeeds
    assert r.json()["status"] == "finalized"
    assert r.json()["evidence_frozen"] is False
    r = api_client.get(f"/api/artifacts/versions/{vid}/evidence")
    assert r.status_code == 409  # no evidence frozen


def test_evidence_errors(api_client):
    r = api_client.get("/api/artifacts/versions/nope/evidence")
    assert r.status_code == 404
    r = api_client.post("/api/artifacts/versions/nope/evidence/verify")
    assert r.status_code == 404

    # Existing but not finalized → 409.
    r = api_client.post(
        "/api/artifacts/lineages",
        json={"project_id": "proj-1", "name": "r", "kind": "report"},
    )
    lineage_id = r.json()["lineage_id"]
    r = api_client.post(
        f"/api/artifacts/lineages/{lineage_id}/versions",
        json={"content": "x"},
    )
    vid = r.json()["version_id"]
    r = api_client.get(f"/api/artifacts/versions/{vid}/evidence")
    assert r.status_code == 409
    r = api_client.post(f"/api/artifacts/versions/{vid}/evidence/verify")
    assert r.status_code == 409
