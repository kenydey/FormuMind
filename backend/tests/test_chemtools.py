"""ChemCrow tool gateway tests (services/chemtools.py).

ChemCrow is not installed in CI, so these tests exercise two contracts:
1. degradation invariance — every gateway call returns a neutral value when
   chemcrow/rdkit are absent or the gateway is disabled;
2. tool-output parsing — via a fake ``chemcrow.tools`` module injected into
   ``sys.modules``.
"""
from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import chemtools


@pytest.fixture(autouse=True)
def _fresh_gateway(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    chemtools.clear_cache()
    yield
    get_settings.cache_clear()
    chemtools.clear_cache()


def _install_fake_chemcrow(monkeypatch, **tool_outputs):
    """Register a fake ``chemcrow`` package whose tools echo canned outputs.

    ``tool_outputs`` maps tool class name -> either a string to return or a
    callable(arg) -> str.
    """
    tools_mod = types.ModuleType("chemcrow.tools")
    for name, output in tool_outputs.items():
        def make_cls(out):
            class _Tool:
                calls: list[str] = []

                def _run(self, arg):
                    type(self).calls.append(arg)
                    return out(arg) if callable(out) else out

            return _Tool

        setattr(tools_mod, name, make_cls(output))
    pkg = types.ModuleType("chemcrow")
    pkg.tools = tools_mod
    monkeypatch.setitem(sys.modules, "chemcrow", pkg)
    monkeypatch.setitem(sys.modules, "chemcrow.tools", tools_mod)
    return tools_mod


# ── degradation invariance (chemcrow absent in CI) ───────────────────────────


def test_all_calls_neutral_without_chemcrow():
    assert chemtools.name_to_smiles("epoxy resin") is None
    assert chemtools.name_to_cas("epoxy resin") is None
    assert chemtools.patent_check("CCO") is None
    assert chemtools.controlled_check("CCO") is None
    assert chemtools.explosive_check("64-17-5") is None
    assert chemtools.safety_flags("CCO", "64-17-5") == {
        "controlled": None,
        "explosive": None,
    }
    # func_groups falls back to rdkit; rdkit also absent in CI -> []
    if not chemtools.rdkit_available():
        assert chemtools.func_groups("CCO") == []
        assert chemtools.mol_similarity("CCO", "CCN") is None


def test_gateway_disabled_short_circuits(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CHEMTOOLS_ENABLED", "false")
    get_settings.cache_clear()
    _install_fake_chemcrow(monkeypatch, Query2SMILES="CCO")
    assert chemtools.gateway_enabled() is False
    assert chemtools.name_to_smiles("ethanol") is None
    assert chemtools.func_groups("CCO") == []
    assert chemtools.patent_check("CCO") is None


def test_availability_report_shape():
    report = chemtools.availability()
    assert report["enabled"] is True
    assert "capabilities" in report
    caps = report["capabilities"]
    for key in (
        "name_to_smiles",
        "func_groups",
        "mol_similarity",
        "patent_check",
        "controlled_check",
        "explosive_check",
        "web_search",
    ):
        assert "available" in caps[key]
        if not caps[key]["available"]:
            assert caps[key]["hint"]


# ── tool-output parsing via fake chemcrow ────────────────────────────────────


def test_name_to_smiles_accepts_smiles_and_rejects_prose(monkeypatch):
    _install_fake_chemcrow(monkeypatch, Query2SMILES="CC(=O)Oc1ccccc1C(=O)O")
    assert chemtools.name_to_smiles("aspirin") == "CC(=O)Oc1ccccc1C(=O)O"

    chemtools.clear_cache()
    _install_fake_chemcrow(
        monkeypatch, Query2SMILES="Could not find a molecule matching the text."
    )
    assert chemtools.name_to_smiles("blorbium") is None


def test_name_to_cas_extracts_cas_from_prose(monkeypatch):
    _install_fake_chemcrow(monkeypatch, Query2CAS="CAS number: 50-78-2.")
    assert chemtools.name_to_cas("aspirin") == "50-78-2"


def test_patent_check_parses_verdicts(monkeypatch):
    _install_fake_chemcrow(monkeypatch, PatentCheck="Patented")
    assert chemtools.patent_check("CCO") is True

    chemtools.clear_cache()
    _install_fake_chemcrow(monkeypatch, PatentCheck="Novel")
    assert chemtools.patent_check("CCN") is False

    chemtools.clear_cache()
    _install_fake_chemcrow(monkeypatch, PatentCheck="???")
    assert chemtools.patent_check("CCC") is None


def test_controlled_check_parses_both_aliases(monkeypatch):
    _install_fake_chemcrow(
        monkeypatch,
        ControlChemCheck="This molecule appears in a list of controlled chemicals.",
    )
    assert chemtools.controlled_check("CCO") is True

    chemtools.clear_cache()
    # Older releases exported ControlledChemicalCheck instead.
    _install_fake_chemcrow(
        monkeypatch, ControlledChemicalCheck="not found in any controlled list"
    )
    assert chemtools.controlled_check("CCN") is False


def test_explosive_check_requires_cas_format(monkeypatch):
    _install_fake_chemcrow(monkeypatch, ExplosiveCheck="Molecule is an explosive")
    assert chemtools.explosive_check("not-a-cas") is None
    assert chemtools.explosive_check("121-82-4") is True

    chemtools.clear_cache()
    _install_fake_chemcrow(
        monkeypatch, ExplosiveCheck="Molecule is not known to be explosive"
    )
    assert chemtools.explosive_check("64-17-5") is False


def test_func_groups_parses_chemcrow_prose(monkeypatch):
    _install_fake_chemcrow(
        monkeypatch,
        FuncGroups="This molecule contains hydroxyl groups, ester groups, and aromatic rings.",
    )
    groups = chemtools.func_groups("CC(=O)Oc1ccccc1C(=O)O")
    assert "hydroxyl groups" in groups
    assert "ester groups" in groups
    assert "aromatic rings" in groups


def test_results_are_cached(monkeypatch):
    tools = _install_fake_chemcrow(monkeypatch, Query2SMILES="CCO")
    assert chemtools.name_to_smiles("ethanol") == "CCO"
    assert chemtools.name_to_smiles("ethanol") == "CCO"
    assert len(tools.Query2SMILES.calls) == 1


def test_timeout_returns_neutral(monkeypatch):
    import time as _time

    def slow(_arg):
        _time.sleep(0.5)
        return "CCO"

    monkeypatch.setenv("FORMUMIND_CHEMTOOLS_TIMEOUT_S", "0.05")
    get_settings.cache_clear()
    _install_fake_chemcrow(monkeypatch, Query2SMILES=slow)
    assert chemtools.name_to_smiles("ethanol") is None


def test_failures_are_not_cached(monkeypatch):
    _install_fake_chemcrow(
        monkeypatch, Query2SMILES="Could not find a molecule matching the text."
    )
    assert chemtools.name_to_smiles("obscurine") is None
    # Second attempt with a working tool must not be poisoned by the miss.
    _install_fake_chemcrow(monkeypatch, Query2SMILES="C1CC1")
    assert chemtools.name_to_smiles("obscurine") == "C1CC1"


# ── chemical_profile aggregation ─────────────────────────────────────────────


def test_chemical_profile_superset_without_chemcrow():
    profile = chemtools.chemical_profile("Zinc phosphate")  # catalog hit
    # Base lookup fields survive untouched
    assert profile["found"] is True
    assert profile["source"] == "catalog"
    # ChemCrow-backed fields degrade to neutral
    assert isinstance(profile["func_groups"], list)
    assert profile["safety"]["controlled"] is None
    assert profile["safety"]["explosive"] is None
    assert profile["chemtools"]["enabled"] is True


def test_chemical_profile_gap_fills_smiles(monkeypatch):
    _install_fake_chemcrow(
        monkeypatch,
        Query2SMILES="CC(=O)Oc1ccccc1C(=O)O",
        Query2CAS="50-78-2",
        FuncGroups="This molecule contains ester groups.",
        PatentCheck="Novel",
        ControlChemCheck="not found",
        ExplosiveCheck="not explosive",
    )
    # Force the base lookup to miss so tier-4 / profile gap-fill kicks in.
    monkeypatch.setattr(
        "app.services.chemical_lookup.lookup_chemical",
        lambda q: {
            "query": q, "cas": "", "iupac_name": q, "zh_name": "", "formula": "",
            "smiles": None, "molar_mass": None, "found": False, "source": "none",
        },
    )
    profile = chemtools.chemical_profile("aspirin")
    assert profile["smiles"] == "CC(=O)Oc1ccccc1C(=O)O"
    assert profile["cas"] == "50-78-2"
    assert profile["source"] == "chemcrow"
    assert profile["patented"] is False
    assert profile["safety"]["controlled"] is False
    assert "ester groups" in profile["func_groups"]


# ── lookup tier 4 ────────────────────────────────────────────────────────────


def test_lookup_chemical_tier4_chemcrow(monkeypatch):
    from app.services import chemical_lookup

    _install_fake_chemcrow(monkeypatch, Query2SMILES="C1CO1", Query2CAS="75-21-8")
    monkeypatch.setattr(chemical_lookup, "_lookup_catalog", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_pubchem", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_offline_compounds", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_CACHE", {})
    hit = chemical_lookup.lookup_chemical("ethylene oxide")
    assert hit["found"] is True
    assert hit["source"] == "chemcrow"
    assert hit["smiles"] == "C1CO1"
    assert hit["cas"] == "75-21-8"


# ── API endpoints ────────────────────────────────────────────────────────────


def test_profile_endpoint_returns_neutral_fields():
    client = TestClient(app)
    resp = client.get("/api/chemical/profile", params={"q": "Zinc phosphate"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert "func_groups" in data
    assert "safety" in data
    assert "patented" in data


def test_tools_status_endpoint():
    client = TestClient(app)
    resp = client.get("/api/chemical/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert "capabilities" in data
    assert data["chemcrow_installed"] in (True, False)
