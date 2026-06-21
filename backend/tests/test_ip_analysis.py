"""Tests for IP analysis service — offline keyword fallback."""
import pytest

from app.domain.knowledge import baseline_formulation
from app.domain.schemas import IPAnalysisRequest, ProductDomain, Requirement
from app.services.ip_analysis import (
    _extract_chem_terms,
    _offline_keyword_analysis,
    _search_relevant_patents,
    analyze_ip_risk,
)


_REQ = Requirement(domain=ProductDomain.anticorrosion_coating)


def test_extract_chem_terms_returns_nonzero_ingredients():
    form = baseline_formulation(_REQ)
    terms = _extract_chem_terms(form)
    assert len(terms) >= 3
    assert any("epoxy" in t.lower() or "zinc" in t.lower() for t in terms)


def test_search_relevant_patents_returns_evidence():
    form = baseline_formulation(_REQ)
    terms = _extract_chem_terms(form)
    patents = _search_relevant_patents(terms, form.domain, limit=4)
    assert len(patents) >= 1
    assert all(hasattr(p, "identifier") for p in patents)


def test_offline_analysis_returns_report():
    form = baseline_formulation(_REQ)
    terms = _extract_chem_terms(form)
    patents = _search_relevant_patents(terms, form.domain, limit=4)
    report = _offline_keyword_analysis(form, patents)
    assert 0.0 <= report.novelty_score <= 1.0
    assert report.engine == "offline-keyword"
    assert isinstance(report.risks, list)
    assert isinstance(report.whitespace_hints, list)


def test_analyze_ip_risk_full_pipeline():
    form = baseline_formulation(_REQ)
    req = IPAnalysisRequest(formulation=form, limit_patents=4)
    report = analyze_ip_risk(req)
    assert report.formulation_name == form.name
    assert 0.0 <= report.novelty_score <= 1.0
    assert report.raw_patents_searched >= 1
    assert report.engine in ("llm", "offline-keyword")


def test_analyze_ip_risk_degreaser_domain():
    form = baseline_formulation(Requirement(domain=ProductDomain.degreaser))
    req = IPAnalysisRequest(formulation=form, limit_patents=3)
    report = analyze_ip_risk(req)
    assert len(report.whitespace_hints) >= 1


def test_ip_report_has_valid_risk_levels():
    form = baseline_formulation(_REQ)
    req = IPAnalysisRequest(formulation=form)
    report = analyze_ip_risk(req)
    valid_levels = {"high", "medium", "low", "unknown"}
    for risk in report.risks:
        assert risk.risk in valid_levels
