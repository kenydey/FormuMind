"""P1-R1 recommend Top-N and diversity tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import Ingredient, ObjectiveSpec, ProductDomain, Requirement
from app.domain.schemas import Formulation
from app.services.recommend_diversity import select_diverse_mmr


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _req() -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        salt_spray_hours=800,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", weight=0.6, direction="maximize"),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.4, direction="minimize"),
        ],
    )


def _form(name: str, score: float, ingredients: list[str]) -> Formulation:
    return Formulation(
        name=name,
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name=n, role="resin", weight_pct=50.0 / len(ingredients)) for n in ingredients],
        score=score,
        predicted={"salt_spray_hours": score * 100, "cost_cny_per_kg": 20 - score},
    )


def test_default_n_is_six(monkeypatch):
    """N-01: omit n → requested_n=6."""
    from app.main import app

    client = TestClient(app)
    res = client.post(
        "/api/formulations/recommend",
        json={"requirement": _req().model_dump()},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["requested_n"] == 6


def test_legacy_n_three_compatible(monkeypatch):
    """C-01: explicit n=3 unchanged."""
    from app.main import app

    client = TestClient(app)
    res = client.post(
        "/api/formulations/recommend",
        json={"requirement": _req().model_dump(), "n": 3},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["requested_n"] == 3
    assert len(body["scored"]) <= 3
    assert "formulas" in body


def test_mmr_increases_diversity():
    """N-04: MMR picks less similar high-score set."""
    forms = [
        _form("A1", 0.95, ["Epoxy A", "Zinc phosphate", "Water"]),
        _form("A2", 0.94, ["Epoxy A", "Zinc phosphate", "Amine"]),
        _form("A3", 0.93, ["Epoxy A", "Zinc phosphate", "Silica"]),
        _form("B1", 0.80, ["Acrylic B", "Chrome free", "Silane"]),
        _form("C1", 0.78, ["Polyurethane C", "Barium sulfate", "Talc"]),
    ]
    top_score, _ = select_diverse_mmr(forms, 3, lambda_score=0.7)
    names = {f.name for f in top_score}
    assert "B1" in names or "C1" in names
    assert len(names) == 3
