"""Tests for substrate-aware DOE levers and research query injection."""
import pytest

from app.domain.knowledge import baseline_formulation, offline_recommend_fallback
from app.domain.levers import substrate_default_levers
from app.domain.project_spec import levers_to_doe_factors, resolve_levers
from app.domain.research_query import build_research_query
from app.domain.schemas import ProductDomain, Requirement, Substrate
from app.pipeline import reconstruct


def _mg_req() -> Requirement:
    return Requirement(
        domain=ProductDomain.surface_treatment,
        substrate=Substrate.magnesium_alloy,
        product_type="镁合金表面钝化剂",
        salt_spray_hours=128,
    )


def test_mg_alloy_doe_factors_use_gl_and_process():
    req = _mg_req()
    levers = resolve_levers(req)
    names = [lev.name for lev in levers]
    assert "Phosphoric acid" not in names
    assert "Hexafluorozirconic acid" in names
    assert "immersion_time_min" in names
    assert "bath_temperature_c" in names
    h2zrf = next(l for l in levers if l.name == "Hexafluorozirconic acid")
    assert h2zrf.unit == "g/L"
    factors = levers_to_doe_factors(levers)
    assert len(factors) >= 5


def test_steel_phosphate_unchanged():
    req = Requirement(domain=ProductDomain.surface_treatment, substrate=Substrate.carbon_steel)
    levers = resolve_levers(req)
    names = [lev.name for lev in levers]
    assert "Phosphoric acid" in names
    assert "immersion_time_min" not in names


def test_formulation_from_factors_respects_substrate():
    req = _mg_req()
    form = reconstruct.formulation_from_factors(
        req,
        {
            "Hexafluorozirconic acid": 2.0,  # 2 g/L → 0.2 wt%
            "(3-Aminopropyl)triethoxysilane (APTES)": 1.0,
            "Cerium nitrate": 0.5,
            "immersion_time_min": 120.0,
        },
    )
    zr = next(i for i in form.ingredients if i.name == "Hexafluorozirconic acid")
    assert zr.weight_pct == pytest.approx(0.2, abs=0.01)
    assert "Phosphoric acid" not in [i.name for i in form.ingredients]


def test_offline_recommend_fallback_perturbs_mg_active_not_phosphoric():
    req = _mg_req()
    variants = offline_recommend_fallback(req, n=3)
    assert len(variants) >= 2
    base = baseline_formulation(req)
    assert all("Phosphoric acid" not in [i.name for i in v.ingredients] for v in variants)
    assert variants[0].ingredients[0].name == base.ingredients[0].name


def test_build_research_query_injects_magnesium_terms():
    req = _mg_req()
    q = build_research_query("passivation coating", req)
    assert "passivation coating" in q
    assert "magnesium" in q.lower()
    assert "镁合金" in q or "AZ91" in q


def test_substrate_default_levers_only_surface_treatment():
    req = Requirement(domain=ProductDomain.degreaser, substrate=Substrate.magnesium_alloy)
    assert substrate_default_levers(req) is None
