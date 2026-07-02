"""Tests for requirement upgrade: constraint_values, chemical lookup, manual/modify APIs."""
import time

import pytest
from fastapi.testclient import TestClient

from app.domain.schemas import Formulation, Ingredient, ProductDomain, Requirement
from app.main import app
from app.services.chemical_lookup import lookup_chemical

client = TestClient(app)


def _poll_status(status_url: str, *, timeout_s: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get(status_url).json()
        if last.get("state") in ("completed", "failed"):
            return last
        time.sleep(0.05)
    return last


def test_requirement_constraint_values_roundtrip():
    req = Requirement(
        domain=ProductDomain.surface_treatment,
        constraint_values={"浸渍时间上限": 120.0, "浴温上限": 50.0},
    )
    data = req.model_dump()
    assert data["constraint_values"]["浸渍时间上限"] == 120.0


def test_ingredient_zh_name_field():
    ing = Ingredient(name="Zinc phosphate", zh_name="磷酸锌", role="inhibitor", weight_pct=5.0)
    assert ing.zh_name == "磷酸锌"


def test_chemical_lookup_catalog():
    hit = lookup_chemical("Zinc phosphate")
    assert hit["found"] is True
    assert hit["source"] == "catalog"


def test_chemical_lookup_api():
    r = client.get("/api/chemical/lookup", params={"q": "Zinc phosphate"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True


def test_manual_formulation_endpoint():
    form = Formulation(
        name="Manual test",
        domain=ProductDomain.degreaser,
        ingredients=[Ingredient(name="Sodium hydroxide", role="builder", weight_pct=5.0)],
        rationale="manual",
    )
    r = client.post("/api/formulations/manual", json={"formulation": form.model_dump()})
    assert r.status_code == 200
    assert r.json()["formulation"]["name"] == "Manual test"


def test_modify_research_endpoint_returns_202():
    req = Requirement(domain=ProductDomain.degreaser, cleaning_efficiency=90)
    r = client.post(
        "/api/research/modify",
        json={
            "requirement": req.model_dump(),
            "modify_prompt": "降低 VOC，增加表面活性剂",
            "n": 2,
        },
    )
    assert r.status_code == 202
    handle = r.json()
    assert handle["task_id"]
    assert handle["stream_url"]


def test_modify_research_completes_via_status():
    req = Requirement(domain=ProductDomain.degreaser, cleaning_efficiency=90)
    r = client.post(
        "/api/research/modify",
        json={
            "requirement": req.model_dump(),
            "modify_prompt": "降低 VOC，增加表面活性剂",
            "n": 2,
        },
    )
    assert r.status_code == 202
    handle = r.json()
    st = _poll_status(handle["status_url"], timeout_s=60.0)
    assert st["state"] == "completed", st
    research = st["result"]["research"]
    assert len(research.get("recommended", [])) >= 1


def test_formulations_modify_removed():
    req = Requirement(domain=ProductDomain.degreaser)
    r = client.post(
        "/api/formulations/modify",
        json={"requirement": req.model_dump(), "modify_prompt": "test"},
    )
    assert r.status_code == 404
