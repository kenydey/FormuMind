"""Tests for GET /api/meta/default-levers."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.project_spec import default_levers_for
from app.domain.schemas import ProductDomain, Substrate
from app.main import app

client = TestClient(app)


def test_default_levers_for_anticorrosion_includes_cure():
    levers = default_levers_for(
        ProductDomain.anticorrosion_coating,
        Substrate.carbon_steel,
        cure_temperature_c=80.0,
    )
    names = [lev.name for lev in levers]
    assert "Zinc phosphate" in names
    assert "cure_temperature_c" in names


def test_default_levers_surface_treatment_steel_from_substrate_ssot():
    levers = default_levers_for(ProductDomain.surface_treatment, Substrate.carbon_steel)
    names = [lev.name for lev in levers]
    assert "Phosphoric acid" in names
    assert "immersion_time_min" not in names


def test_default_levers_surface_treatment_mg_alloy():
    levers = default_levers_for(ProductDomain.surface_treatment, Substrate.magnesium_alloy)
    names = [lev.name for lev in levers]
    assert "Hexafluorozirconic acid" in names
    assert "Phosphoric acid" not in names


def test_api_default_levers_endpoint():
    r = client.get(
        "/api/meta/default-levers",
        params={
            "domain": "anticorrosion_coating",
            "substrate": "carbon_steel",
            "cure_temperature_c": 80,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["levers"]) >= 3
    assert any(lev["name"] == "Zinc phosphate" for lev in body["levers"])
