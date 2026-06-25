"""Tests for POST /api/formulations/recommend."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.schemas import ObjectiveSpec, ProductDomain, Requirement
from app.main import app

client = TestClient(app)


def _req() -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        salt_spray_hours=800,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", weight=0.6, direction="maximize"),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.4, direction="minimize"),
        ],
    )


def test_recommend_formulations_offline():
    res = client.post(
        "/api/formulations/recommend",
        json={"requirement": _req().model_dump(), "n": 3},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["engine"] == "offline"
    assert len(body["formulas"]) >= 1
    assert len(body["scored"]) >= 1
    comp = body["formulas"][0]["components"][0]
    assert "name" in comp
    assert "cas_no" in comp or comp.get("mf")


def test_recommend_formulations_explicit_objectives():
    res = client.post(
        "/api/formulations/recommend",
        json={
            "requirement": Requirement(domain=ProductDomain.degreaser).model_dump(),
            "objectives": [
                {"metric": "cleaning_efficiency", "weight": 1.0, "direction": "maximize"},
            ],
            "n": 2,
        },
    )
    assert res.status_code == 200
    assert len(res.json()["formulas"]) <= 2


def test_recommend_formulations_invalid_n():
    res = client.post(
        "/api/formulations/recommend",
        json={"requirement": _req().model_dump(), "n": 0},
    )
    assert res.status_code == 422
