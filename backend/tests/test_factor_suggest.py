"""Tests for DOE factor suggestions (Sprint 3)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.schemas import ProductDomain, Requirement
from app.main import app
from app.services.factor_suggest import suggest_factors


def test_suggest_factors_from_levers():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=800)
    candidates = suggest_factors(req)
    assert len(candidates) >= 2
    names = {c.name for c in candidates}
    assert any("Zinc" in n or "epoxy" in n.lower() or "Polyamide" in n for n in names)


def test_suggest_factors_api():
    client = TestClient(app)
    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    res = client.post("/api/doe/suggest-factors", json=req.model_dump())
    assert res.status_code == 200
    body = res.json()
    assert body["count"] >= 1
    assert body["factors"][0]["name"]
