"""Tests for chemical lookup API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_chemical_lookup_local_material():
    res = client.get("/api/chemical/lookup", params={"q": "Bisphenol-A epoxy (DGEBA)"})
    assert res.status_code == 200
    body = res.json()
    assert "cas" in body
    assert body.get("iupac_name")


def test_chemical_lookup_empty_query_rejected():
    res = client.get("/api/chemical/lookup", params={"q": ""})
    assert res.status_code == 422
