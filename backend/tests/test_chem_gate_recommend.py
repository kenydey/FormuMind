"""Phase C tests — formulation gate native gap-fill, chem screening on
recommend paths, functional-group prompt block, and molbloom IP checks.

(ChemCrow removed 2026-09 — these exercise the native PubChem/RDKit/molbloom
gateway behind the same public functions.)
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.formulation_gate import enrich_component, enrich_ingredient
from app.domain.schemas import (
    Formulation,
    Ingredient,
    ProductDomain,
    RecommendedFormulaComponent,
    Requirement,
    Substrate,
)
from app.services import chemtools


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    chemtools.clear_cache()
    yield
    get_settings.cache_clear()
    chemtools.clear_cache()


def _stub_pubchem(monkeypatch, *, smiles: str | None = None, cas: str | None = None, spy: list | None = None):
    """Route PubChem lookups to canned SMILES/CAS answers (no network)."""
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: True)

    def fake_get(path: str):
        if spy is not None:
            spy.append(path)
        if "/property/" in path and smiles is not None:
            return {"PropertyTable": {"Properties": [{"SMILES": smiles}]}}
        if "/synonyms/" in path and cas is not None:
            return {"InformationList": {"Information": [{"Synonym": [cas]}]}}
        return None

    monkeypatch.setattr(chemtools, "_pubchem_get", fake_get)


def _stub_molbloom(monkeypatch, patented: bool | None):
    monkeypatch.setattr(chemtools, "molbloom_available", lambda: patented is not None)
    monkeypatch.setattr(chemtools, "_molbloom_patented", lambda smiles: patented)


def _form(**ing_kwargs) -> Formulation:
    defaults = dict(name="Epoxy resin X", role="resin", weight_pct=50.0)
    defaults.update(ing_kwargs)
    return Formulation(
        name="test",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(**defaults)],
        rationale="t",
    )


# ── gate gap-fill ────────────────────────────────────────────────────────────


def test_enrich_ingredient_unchanged_offline(monkeypatch):
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: False)
    ing = Ingredient(name="mystery resin", role="resin", weight_pct=50.0)
    out = enrich_ingredient(ing)
    assert out.smiles is None
    assert out.cas_no is None


def test_enrich_ingredient_gap_fills_via_pubchem(monkeypatch):
    _stub_pubchem(monkeypatch, smiles="C1CO1", cas="75-21-8")
    ing = Ingredient(name="mystery oxirane", role="resin", weight_pct=50.0)
    out = enrich_ingredient(ing)
    assert out.smiles == "C1CO1"
    assert out.cas_no == "75-21-8"


def test_enrich_ingredient_catalog_wins_over_gateway(monkeypatch):
    smiles_calls: list[str] = []
    _stub_pubchem(monkeypatch, smiles="CCO", cas="2855-13-2", spy=smiles_calls)
    # Catalog has curated SMILES for IPDA — gateway must not override it and
    # must only be consulted for the field the catalog lacks (CAS).
    ing = Ingredient(name="Isophorone diamine (IPDA)", role="hardener", weight_pct=20.0)
    out = enrich_ingredient(ing)
    assert out.smiles == "CC1(C)CC(N)CC(C)(CN)C1"  # curated value wins
    assert smiles_calls == []
    assert out.cas_no == "2855-13-2"  # missing field gap-filled


def test_enrich_component_gap_fills_via_pubchem(monkeypatch):
    _stub_pubchem(monkeypatch, smiles="CCN", cas="75-04-7")
    comp = RecommendedFormulaComponent(name="mystery amine", weight_pct=5.0)
    out = enrich_component(comp)
    assert out.smiles == "CCN"
    assert out.cas_no == "75-04-7"


# ── formulation screening ────────────────────────────────────────────────────


def test_screen_formulation_empty_offline(monkeypatch):
    monkeypatch.setattr(chemtools, "molbloom_available", lambda: False)
    assert chemtools.screen_formulation(_form(smiles="CCO")) == []


def test_screen_formulation_flags_patented(monkeypatch):
    _stub_molbloom(monkeypatch, patented=True)
    warnings = chemtools.screen_formulation(_form(smiles="CCO"))
    assert any("IP 预筛" in w for w in warnings)


def test_screen_formulation_skips_trace_and_smiles_less(monkeypatch):
    _stub_molbloom(monkeypatch, patented=True)
    trace = _form(smiles="CCO", weight_pct=0.1)  # below threshold
    assert chemtools.screen_formulation(trace) == []
    no_smiles = _form(smiles=None)
    assert chemtools.screen_formulation(no_smiles) == []


def test_score_and_validate_screens_only_when_asked(monkeypatch):
    from app.pipeline.workflow import _score_and_validate

    _stub_molbloom(monkeypatch, patented=True)
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating, substrate=Substrate.carbon_steel
    )
    plain = _score_and_validate(_form(smiles="CCO"), None, req)
    assert not any("IP 预筛" in w for w in plain.warnings)
    screened = _score_and_validate(_form(smiles="CCO"), None, req, chem_screen=True)
    assert any("IP 预筛" in w for w in screened.warnings)


# ── prompt block ─────────────────────────────────────────────────────────────

_DGEBA = "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1"


def test_func_groups_prompt_block_empty_offline():
    from app.services.llm import _func_groups_prompt_block

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate=Substrate.carbon_steel,
        materials=[{"name": "Bisphenol-A epoxy (DGEBA)", "role": "resin", "smiles": _DGEBA}],
    )
    block = _func_groups_prompt_block(req, None)
    # RDKit absent in a bare env -> no groups resolvable -> block omitted
    if not chemtools.rdkit_available():
        assert block == ""


def test_func_groups_prompt_block_lists_groups():
    from app.services.llm import _func_groups_prompt_block

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate=Substrate.carbon_steel,
        materials=[{"name": "DGEBA", "role": "resin", "smiles": _DGEBA}],
    )
    block = _func_groups_prompt_block(req, None)
    if chemtools.rdkit_available():
        assert "DGEBA" in block
        assert "epoxide" in block  # RDKit epoxide SMARTS label
        assert "aromatic ring" in block


# ── IP molecule checks ───────────────────────────────────────────────────────


def test_ip_report_molecule_checks_empty_without_molbloom(monkeypatch):
    from app.domain.schemas import IPAnalysisRequest
    from app.services.ip_analysis import analyze_ip_risk

    monkeypatch.setattr(chemtools, "molbloom_available", lambda: False)
    monkeypatch.setattr(
        "app.services.ip_analysis._search_relevant_patents", lambda *a, **k: []
    )
    report = analyze_ip_risk(IPAnalysisRequest(formulation=_form(smiles="CCO")))
    assert report.molecule_checks == []


def test_ip_report_carries_molecule_checks(monkeypatch):
    from app.domain.schemas import IPAnalysisRequest
    from app.services.ip_analysis import analyze_ip_risk

    _stub_molbloom(monkeypatch, patented=True)
    monkeypatch.setattr(
        "app.services.ip_analysis._search_relevant_patents", lambda *a, **k: []
    )
    report = analyze_ip_risk(IPAnalysisRequest(formulation=_form(smiles="CCO")))
    assert len(report.molecule_checks) == 1
    check = report.molecule_checks[0]
    assert check.name == "Epoxy resin X"
    assert check.patented is True


# --- PubChem 查询前置过滤（journal-citation artifacts） ---


@pytest.mark.parametrize(
    "name,expected",
    [
        # 期刊卷期引用形态 → 拦截（不再产生 PubChem 往返）
        ("Calphad 2001", False),
        ("Ferroelectrics 76", False),
        ("Ferroelectrics 553", False),
        ("Soc 1972", False),
        ("Energy 2014", False),
        ("Chemie 59", False),
        ("Nanoscale 2018", False),
        ("We 14", False),
        # OCR 碎片 / 空 → 拦截
        ("T", False),
        ("-", False),
        ("", False),
        # 真化学物 / 商标 → 放行（单次 404 无害）
        ("Kapton", True),
        ("Sigraflex", True),
        ("magnesium stearate", True),
        ("Calcium carbonate", True),
    ],
)
def test_is_queryable_name_filters_citation_artifacts(name, expected):
    assert chemtools._is_queryable_name(name) is expected
