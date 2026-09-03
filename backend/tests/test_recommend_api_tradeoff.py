"""P1-R2 recommend API trade-off integration."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import ObjectiveSpec, ProductDomain, Requirement


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_RECOMMEND_TRADEOFF_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _req() -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", direction="maximize"),
            ObjectiveSpec(metric="cost_cny_per_kg", direction="minimize"),
        ],
    )


def test_recommend_includes_tradeoff():
    from app.main import app

    res = TestClient(app).post(
        "/api/formulations/recommend",
        json={"requirement": _req().model_dump(), "n": 3, "include_tradeoff": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("tradeoff") is not None
    assert body["tradeoff"]["comparison_table"]
    assert body["tradeoff"]["scenario_picks"]
