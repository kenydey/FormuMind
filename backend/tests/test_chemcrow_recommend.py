"""Phase C tests — formulation gate ChemCrow gap-fill, chem screening on
recommend paths, functional-group prompt block, and molbloom IP checks."""
from __future__ import annotations

import sys
import types

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


def _install_fake_chemcrow(monkeypatch, **tool_outputs):
    tools_mod = types.ModuleType("chemcrow.tools")
    for name, output in tool_outputs.items():
        def make_cls(out):
            class _Tool:
                def _run(self, arg):
                    return out(arg) if callable(out) else out

            return _Tool

        setattr(tools_mod, name, make_cls(output))
    pkg = types.ModuleType("chemcrow")
    pkg.tools = tools_mod
    monkeypatch.setitem(sys.modules, "chemcrow", pkg)
    monkeypatch.setitem(sys.modules, "chemcrow.tools", tools_mod)


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


def test_enrich_ingredient_unchanged_without_chemcrow():
    ing = Ingredient(name="mystery resin", role="resin", weight_pct=50.0)
    out = enrich_ingredient(ing)
    assert out.smiles is None
    assert out.cas_no is None


def test_enrich_ingredient_gap_fills_via_gateway(monkeypatch):
    _install_fake_chemcrow(monkeypatch, Query2SMILES="C1CO1", Query2CAS="75-21-8")
    ing = Ingredient(name="mystery oxirane", role="resin", weight_pct=50.0)
    out = enrich_ingredient(ing)
    assert out.smiles == "C1CO1"
    assert out.cas_no == "75-21-8"


def test_enrich_ingredient_catalog_wins_over_gateway(monkeypatch):
    smiles_calls: list[str] = []

    def smiles_spy(arg):
        smiles_calls.append(arg)
        return "CCO"

    _install_fake_chemcrow(monkeypatch, Query2SMILES=smiles_spy, Query2CAS="2855-13-2")
    # Catalog has curated SMILES for IPDA — gateway must not override it and
    # must only be consulted for the field the catalog lacks (CAS).
    ing = Ingredient(name="Isophorone diamine (IPDA)", role="hardener", weight_pct=20.0)
    out = enrich_ingredient(ing)
    assert out.smiles == "CC1(C)CC(N)CC(C)(CN)C1"  # curated value wins
    assert smiles_calls == []
    assert out.cas_no == "2855-13-2"  # missing field gap-filled


def test_enrich_component_gap_fills_via_gateway(monkeypatch):
    _install_fake_chemcrow(monkeypatch, Query2SMILES="CCN", Query2CAS="75-04-7")
    comp = RecommendedFormulaComponent(name="mystery amine", weight_pct=5.0)
    out = enrich_component(comp)
    assert out.smiles == "CCN"
    assert out.cas_no == "75-04-7"


# ── formulation screening ────────────────────────────────────────────────────


def test_screen_formulation_empty_without_chemcrow():
    assert chemtools.screen_formulation(_form(smiles="CCO")) == []


def test_screen_formulation_flags_patented_and_controlled(monkeypatch):
    _install_fake_chemcrow(
        monkeypatch,
        PatentCheck="Patented",
        ControlChemCheck="appears in a list of controlled chemicals",
    )
    warnings = chemtools.screen_formulation(_form(smiles="CCO"))
    assert any("IP 预筛" in w for w in warnings)
    assert any("管制" in w for w in warnings)


def test_screen_formulation_skips_trace_and_smiles_less(monkeypatch):
    _install_fake_chemcrow(monkeypatch, PatentCheck="Patented", ControlChemCheck="not found")
    trace = _form(smiles="CCO", weight_pct=0.1)  # below threshold
    assert chemtools.screen_formulation(trace) == []
    no_smiles = _form(smiles=None)
    assert chemtools.screen_formulation(no_smiles) == []


def test_score_and_validate_screens_only_when_asked(monkeypatch):
    from app.pipeline.workflow import _score_and_validate

    _install_fake_chemcrow(
        monkeypatch, PatentCheck="Patented", ControlChemCheck="not found"
    )
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating, substrate=Substrate.carbon_steel
    )
    plain = _score_and_validate(_form(smiles="CCO"), None, req)
    assert not any("IP 预筛" in w for w in plain.warnings)
    screened = _score_and_validate(_form(smiles="CCO"), None, req, chem_screen=True)
    assert any("IP 预筛" in w for w in screened.warnings)


# ── prompt block ─────────────────────────────────────────────────────────────


def test_func_groups_prompt_block_empty_offline():
    from app.services.llm import _func_groups_prompt_block

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate=Substrate.carbon_steel,
        materials=[{"name": "Bisphenol-A epoxy (DGEBA)", "role": "resin",
                    "smiles": "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1"}],
    )
    block = _func_groups_prompt_block(req, None)
    # No chemcrow and no rdkit in CI -> no groups resolvable -> block omitted
    if not chemtools.rdkit_available():
        assert block == ""


def test_func_groups_prompt_block_lists_groups(monkeypatch):
    from app.services.llm import _func_groups_prompt_block

    _install_fake_chemcrow(
        monkeypatch, FuncGroups="This molecule contains epoxide groups and aromatic rings."
    )
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate=Substrate.carbon_steel,
        materials=[{"name": "DGEBA", "role": "resin", "smiles": "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1"}],
    )
    block = _func_groups_prompt_block(req, None)
    assert "DGEBA" in block
    assert "epoxide groups" in block


# ── IP molecule checks ───────────────────────────────────────────────────────


def test_ip_report_molecule_checks_empty_without_chemcrow(monkeypatch):
    from app.domain.schemas import IPAnalysisRequest
    from app.services.ip_analysis import analyze_ip_risk

    monkeypatch.setattr(
        "app.services.ip_analysis._search_relevant_patents", lambda *a, **k: []
    )
    report = analyze_ip_risk(IPAnalysisRequest(formulation=_form(smiles="CCO")))
    assert report.molecule_checks == []


def test_ip_report_carries_molecule_checks(monkeypatch):
    from app.domain.schemas import IPAnalysisRequest
    from app.services.ip_analysis import analyze_ip_risk

    _install_fake_chemcrow(monkeypatch, PatentCheck="Patented")
    monkeypatch.setattr(
        "app.services.ip_analysis._search_relevant_patents", lambda *a, **k: []
    )
    report = analyze_ip_risk(IPAnalysisRequest(formulation=_form(smiles="CCO")))
    assert len(report.molecule_checks) == 1
    check = report.molecule_checks[0]
    assert check.name == "Epoxy resin X"
    assert check.patented is True
