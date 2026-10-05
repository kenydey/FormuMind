"""The Materials panel's "属性补全" button must enrich the catalogue (round-4).

``api.enrichMaterials()`` posted ``{}`` to ``/api/chemical/enrich-materials``, which enriches the materials *it is
given* — an empty list. Nothing was enriched, and the panel reported "属性补全完成: ? 条更新". The catalogue-wide
backfill (the one the startup thread runs) now has its own bounded endpoint.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.material_store import MaterialStore
from app.domain import knowledge
from app.main import app
from app.services import compounds


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db.material_store as material_store_mod

    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/materials.db")
    Base.metadata.create_all(engine)
    store = MaterialStore(make_session_factory(engine))
    monkeypatch.setattr(material_store_mod, "_store", store)
    knowledge.RAW_MATERIALS.refresh()
    yield TestClient(app)
    knowledge.RAW_MATERIALS.refresh()
    get_settings.cache_clear()


def test_a_batch_is_looked_up_filled_and_persisted(client, monkeypatch):
    monkeypatch.setattr(compounds, "_pubchempy_available", lambda: True)
    calls: list[str] = []

    def fake_lookup(name: str):
        calls.append(name)
        return {"smiles": "CCO", "molar_mass": 46.069}

    monkeypatch.setattr(compounds, "lookup", fake_lookup)
    blank = {n: s for n, s in knowledge.RAW_MATERIALS.items() if not (s.get("smiles") and s.get("molar_mass"))}
    assert blank, "the seed catalogue is expected to contain materials without a structure"

    r = client.post("/api/materials/enrich", params={"limit": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["scanned"] == 3 == len(calls)
    assert body["enriched"] >= 1
    assert body["remaining"] == len(blank) - 3
    # persisted: a catalogue refresh (which re-reads the store) still carries what was filled in
    knowledge.RAW_MATERIALS.refresh()
    filled = [n for n in calls if knowledge.RAW_MATERIALS[n].get("smiles")]
    assert filled, "the backfilled SMILES did not survive a refresh"


def test_without_pubchempy_it_says_so_instead_of_pretending(client, monkeypatch):
    monkeypatch.setattr(compounds, "_pubchempy_available", lambda: False)
    body = client.post("/api/materials/enrich").json()
    assert body == {"enriched": 0, "scanned": 0, "remaining": 0, "available": False}


def test_the_batch_size_is_bounded(client):
    assert client.post("/api/materials/enrich", params={"limit": 0}).status_code == 422
    assert client.post("/api/materials/enrich", params={"limit": 101}).status_code == 422
