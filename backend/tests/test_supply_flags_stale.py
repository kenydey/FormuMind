"""Post-A′ #1: stale_price / missing_price into explain supply_flags."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.schemas import Formulation, Ingredient, ProductDomain
from app.services.formulation_explain import build_formulation_explain
from app.services.supply_flags import annotate_supply, collect_formulation_supply_flags


def test_annotate_supply_stale_and_missing():
    old = (datetime.utcnow() - timedelta(days=400)).isoformat(timespec="seconds")
    stale = annotate_supply(
        [{"name": "V", "price_cny_per_kg": 12.0, "price_observed_at": old, "stale_price": True}]
    )
    assert stale["stale_price"] is True
    assert "stale_price" in stale["badges"]

    missing = annotate_supply([{"name": "V", "lead_time_days": 60}])
    assert missing["missing_price"] is True
    assert missing["long_lead_time"] is True


def test_collect_flags_merges_warnings(monkeypatch):
    def _fake(_name: str):
        return {
            "has_suppliers": True,
            "missing_price": False,
            "stale_price": True,
            "long_lead_time": False,
            "badges": ["stale_price"],
            "flags": ["stale_price"],
        }

    monkeypatch.setattr(
        "app.services.supply_flags.supply_annotation_for_material",
        _fake,
    )
    flags = collect_formulation_supply_flags(
        ["Zinc phosphate"],
        existing_warnings=["供应风险：交期长"],
    )
    assert any("交期" in f for f in flags)
    assert any("stale_price" in f for f in flags)


def test_explain_supply_flags_from_annotation(monkeypatch):
    monkeypatch.setattr(
        "app.services.supply_flags.supply_annotation_for_material",
        lambda name: {
            "has_suppliers": True,
            "missing_price": True,
            "stale_price": False,
            "long_lead_time": False,
            "badges": ["missing_price"],
            "flags": ["缺价"],
        },
    )
    form = Formulation(
        name="F",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name="Mystery resin", role="resin", weight_pct=40.0)],
        predicted={"salt_spray_hours": 500.0},
        warnings=[],
    )
    explain = build_formulation_explain(form)
    assert any("缺价" in f for f in explain.supply_flags)
