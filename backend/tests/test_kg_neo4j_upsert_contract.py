"""The Dependency Manager's "write to Neo4j" buttons against the real endpoint (round-4).

The UI posts ``{name, cas_number, smiles}`` (compound) or ``{name}`` (formulation). The endpoint declared
``uid`` as a required field, so every click was a 422 — the form could never succeed — and the success
banner (``r.uid ?? name``) read a field the response never carried. The uid is now derived server-side and
returned.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import neo4j_kg


@pytest.fixture()
def written(monkeypatch):
    calls: dict[str, dict] = {}
    monkeypatch.setattr(neo4j_kg, "is_enabled", lambda: True)
    monkeypatch.setattr(neo4j_kg, "healthcheck", lambda: True)

    def compound(uid, name, **kw):
        calls["compound"] = {"uid": uid, "name": name, **kw}
        return calls.get("_ok", True)

    def formulation(uid, name, **kw):
        calls["formulation"] = {"uid": uid, "name": name, **kw}
        return calls.get("_ok", True)

    monkeypatch.setattr(neo4j_kg, "upsert_compound", compound)
    monkeypatch.setattr(neo4j_kg, "upsert_formulation", formulation)
    return calls


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def test_the_form_payload_of_the_ui_is_accepted_and_the_uid_comes_back(client, written):
    r = client.post("/api/kg/neo4j/compounds", json={"name": "Zinc phosphate", "cas_number": "7779-90-0", "smiles": "[Zn+2]"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "message": "upserted", "uid": "chem:cas:7779-90-0"}
    assert written["compound"]["uid"] == "chem:cas:7779-90-0"
    assert written["compound"]["smiles"] == "[Zn+2]"


def test_without_a_cas_number_the_uid_is_a_name_slug(client, written):
    r = client.post("/api/kg/neo4j/compounds", json={"name": "  Bisphenol-A Epoxy (DGEBA) "})
    assert r.status_code == 200, r.text
    assert r.json()["uid"] == "chem:name:bisphenol-a-epoxy-dgeba"


def test_an_explicit_uid_is_kept(client, written):
    r = client.post("/api/kg/neo4j/compounds", json={"name": "x", "uid": "chem:cas:1-2-3"})
    assert r.json()["uid"] == "chem:cas:1-2-3"
    r = client.post("/api/kg/neo4j/formulations", json={"name": "x", "uid": "my-form"})
    assert r.json()["uid"] == "my-form"


def test_a_formulation_needs_only_a_name(client, written):
    r = client.post("/api/kg/neo4j/formulations", json={"name": "锌系 底漆 A"})
    assert r.status_code == 200, r.text
    assert r.json()["uid"] == "form:锌系-底漆-a"
    assert written["formulation"]["status"] == "draft"


def test_a_failed_write_is_reported_as_such_not_as_success(client, written):
    written["_ok"] = False
    body = client.post("/api/kg/neo4j/compounds", json={"name": "x"}).json()
    assert body["ok"] is False and body["message"] == "failed"
    assert body["uid"] == "chem:name:x"


def test_a_name_is_still_required(client, written):
    assert client.post("/api/kg/neo4j/compounds", json={"cas_number": "1-2-3"}).status_code == 422
