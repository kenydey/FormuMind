"""Tests for complete_structured and offline recommend wrapper."""
from __future__ import annotations

from app.domain.formulation_gate import formulation_to_recommended, offline_recommend_response
from app.domain.knowledge import offline_recommend_fallback
from app.domain.schemas import ProductDomain, RecommendedFormulaListResponse, Requirement
from app.services import llm


def test_offline_recommend_response_wraps_cas():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)
    forms = offline_recommend_fallback(req, n=2)
    resp = offline_recommend_response(forms, reason="test")
    assert resp.engine == "offline"
    assert len(resp.formulas) == 2
    assert resp.formulas[0].components


def test_formulation_to_recommended_has_mf_or_cas():
    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    forms = offline_recommend_fallback(req, n=1)
    rec = formulation_to_recommended(forms[0], engine="offline")
    assert rec.components
    assert rec.components[0].name


def test_complete_structured_no_key_returns_none():
    parsed, err = llm.complete_structured(
        "system",
        "user",
        RecommendedFormulaListResponse,
    )
    assert parsed is None
    assert err
