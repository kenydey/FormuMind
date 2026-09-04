"""Native chemistry gateway tests (services/chemtools.py).

ChemCrow was removed 2026-09 (docs/plans/2026-09-04-dechemcrow.md); the
gateway now calls PubChem REST / RDKit / molbloom directly. These tests
exercise:
1. degradation invariance — every gateway call returns a neutral value when
   the native backends are stubbed out or the gateway is disabled;
2. native-backend behaviour — via stubbed ``_pubchem_get`` /
   ``_molbloom_patented`` and real local RDKit calls.
"""
from __future__ import annotations

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


# ── degradation invariance (backends absent) ────────────────────────────────


def _disable_backends(monkeypatch):
    """Stub every network backend so 'degradation' means 'nothing available'."""
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: False)
    monkeypatch.setattr(chemtools, "molbloom_available", lambda: False)
    monkeypatch.setattr(chemtools, "_pubchem_get", lambda path: None)
    monkeypatch.setattr(chemtools, "_molbloom_patented", lambda smiles: None)


def _pubchem_aspirin(monkeypatch):
    """Route PubChem lookups to a canned aspirin record (no network)."""
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: True)
    monkeypatch.setattr(
        chemtools,
        "_pubchem_get",
        lambda path: (
            {"PropertyTable": {"Properties": [{"CID": 2244, "SMILES": "CC(=O)Oc1ccccc1C(=O)O"}]}}
            if "/property/" in path
            else {"InformationList": {"Information": [{"Synonym": ["aspirin", "50-78-2"]}]}}
        ),
    )


def test_all_calls_neutral_without_backends(monkeypatch):
    _disable_backends(monkeypatch)
    assert chemtools.name_to_smiles("epoxy resin") is None
    assert chemtools.name_to_cas("epoxy resin") is None
    assert chemtools.patent_check("CCO") is None
    assert chemtools.controlled_check("CCO") is None
    assert chemtools.explosive_check("64-17-5") is None
    assert chemtools.safety_flags("CCO", "64-17-5") == {
        "controlled": None,
        "explosive": None,
    }
    # SA score is a local RDKit computation (no network): only degrades when
    # RDKit is absent, same as func_groups / mol_similarity.
    if not chemtools.rdkit_available():
        assert chemtools.synthetic_accessibility("CCO")["sa_score"] is None


def test_gateway_disabled_short_circuits(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CHEMTOOLS_ENABLED", "false")
    get_settings.cache_clear()
    _pubchem_aspirin(monkeypatch)
    assert chemtools.gateway_enabled() is False
    assert chemtools.name_to_smiles("ethanol") is None
    assert chemtools.func_groups("CCO") == []
    assert chemtools.patent_check("CCO") is None
    assert chemtools.synthetic_accessibility("CCO")["sa_score"] is None


def test_availability_report_shape():
    report = chemtools.availability()
    assert report["enabled"] is True
    caps = report["capabilities"]
    for key in (
        "name_to_smiles",
        "name_to_cas",
        "func_groups",
        "mol_similarity",
        "mol_descriptors",
        "synthetic_accessibility",
        "patent_check",
        "explosive_check",
    ):
        assert "available" in caps[key]
        if not caps[key]["available"]:
            assert caps[key]["hint"]


# ── native PubChem name resolution ──────────────────────────────────────────


def test_name_to_smiles_pubchem(monkeypatch):
    _pubchem_aspirin(monkeypatch)
    assert chemtools.name_to_smiles("aspirin") == "CC(=O)Oc1ccccc1C(=O)O"


def test_name_to_smiles_miss_is_none(monkeypatch):
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: True)
    monkeypatch.setattr(chemtools, "_pubchem_get", lambda path: None)
    assert chemtools.name_to_smiles("blorbium") is None


def test_name_to_cas_pubchem(monkeypatch):
    _pubchem_aspirin(monkeypatch)
    assert chemtools.name_to_cas("aspirin") == "50-78-2"


def test_queryable_name_guard_blocks_citation_artifacts():
    # OCR 期刊卷期引用与带型号后缀的商品名（PubChem 永不 resolve）都拦截
    for junk in ("Calphad 2001", "Ferroelectrics 76", "We 14", "J. Mater. 2020", "Epikote 828"):
        assert chemtools._is_queryable_name(junk) is False
    assert chemtools._is_queryable_name("Zinc phosphate") is True


# ── patent / safety screens (native) ────────────────────────────────────────


def test_patent_check_molbloom(monkeypatch):
    monkeypatch.setattr(chemtools, "molbloom_available", lambda: True)
    monkeypatch.setattr(chemtools, "_molbloom_patented", lambda smiles: True)
    assert chemtools.patent_check("CCO") is True

    chemtools.clear_cache()
    monkeypatch.setattr(chemtools, "_molbloom_patented", lambda smiles: False)
    assert chemtools.patent_check("CCN") is False


def test_controlled_check_always_neutral():
    # ChemCrow 0.3.7 shipped no working controlled tool, so the check has
    # always degraded to None here; signature retained for callers.
    assert chemtools.controlled_check("CCO") is None
    assert chemtools.safety_flags("CCO", "64-17-5")["controlled"] is None


def test_explosive_check_pubchem(monkeypatch):
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: True)
    assert chemtools.explosive_check("not-a-cas") is None  # CAS format gate

    def fake_get(path):
        if "/cids/" in path:
            return {"IdentifierList": {"CID": [7434]}}
        return {"Record": {"Section": [{"TOCHeading": "GHS Classification",
                                        "Information": [{"Value": "H201 Explosive"}]}]}}

    monkeypatch.setattr(chemtools, "_pubchem_get", fake_get)
    assert chemtools.explosive_check("121-82-4") is True

    chemtools.clear_cache()

    def fake_clear(path):
        if "/cids/" in path:
            return {"IdentifierList": {"CID": [702]}}
        return {"Record": {"Section": [{"TOCHeading": "GHS Classification",
                                        "Information": [{"Value": "H319 Pictogram"}]}]}}

    monkeypatch.setattr(chemtools, "_pubchem_get", fake_clear)
    assert chemtools.explosive_check("64-17-5") is False


# ── local RDKit structure utilities ─────────────────────────────────────────


def test_func_groups_rdkit():
    groups = chemtools.func_groups("CC(=O)Oc1ccccc1C(=O)O")
    assert "carboxylic acid" in groups
    assert "ester" in groups
    assert "aromatic ring" in groups


def test_func_groups_invalid_smiles_empty():
    assert chemtools.func_groups("not-a-smiles!!") == []


def test_mol_descriptors_rdkit():
    desc = chemtools.mol_descriptors("CC(=O)Oc1ccccc1C(=O)O")
    assert desc is not None
    assert desc["mol_wt"] > 100
    assert "logp" in desc and "tpsa" in desc


def test_mol_similarity_symmetric():
    sim = chemtools.mol_similarity("CCO", "CCO")
    assert sim == 1.0
    sim2 = chemtools.mol_similarity("CCO", "CCN")
    assert 0.0 <= sim2 < 1.0


# ── synthetic accessibility (RDKit SA score) ────────────────────────────────


def test_synthetic_accessibility_easy_molecule():
    out = chemtools.synthetic_accessibility("C1=CC=CC=C1")  # benzene
    assert out["sa_score"] is not None
    assert 1.0 <= out["sa_score"] <= 3.0
    assert out["tier"] == "easy"


def test_synthetic_accessibility_invalid_smiles_unknown():
    out = chemtools.synthetic_accessibility("not-a-smiles!!")
    assert out["sa_score"] is None
    assert out["tier"] == "unknown"


# ── caching behaviour (native path) ─────────────────────────────────────────


def test_results_are_cached(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: True)
    monkeypatch.setattr(
        chemtools,
        "_pubchem_get",
        lambda path: calls.append(path) or {"PropertyTable": {"Properties": [{"SMILES": "CCO"}]}},
    )
    assert chemtools.name_to_smiles("ethanol") == "CCO"
    assert chemtools.name_to_smiles("ethanol") == "CCO"
    assert len(calls) == 1, "the second lookup must be served from cache"


def test_failures_are_cached_briefly(monkeypatch):
    """A miss is cached, but for minutes rather than a day."""
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(chemtools, "_pubchem_get", lambda path: calls.append(path) or None)
    assert chemtools.name_to_smiles("obscurine") is None
    assert chemtools.name_to_smiles("obscurine") is None
    assert len(calls) == 1, "the second lookup must be served from cache"


def test_cached_failures_expire_far_sooner_than_hits():
    assert chemtools._NEGATIVE_CACHE_TTL_SEC < chemtools._CACHE_TTL_SEC / 10


# ── chemical_profile aggregation ─────────────────────────────────────────────


def test_chemical_profile_catalog_hit_keeps_neutral_extras(monkeypatch):
    _disable_backends(monkeypatch)
    profile = chemtools.chemical_profile("Zinc phosphate")  # catalog hit
    assert profile["found"] is True
    assert profile["source"] == "catalog"
    assert isinstance(profile["func_groups"], list)
    assert profile["safety"]["controlled"] is None
    assert profile["safety"]["explosive"] is None
    assert profile["chemtools"]["enabled"] is True
    assert profile["synthetic_accessibility"]["tier"] in ("unknown", "easy", "moderate", "hard", "very_hard")


def test_chemical_profile_gap_fills_smiles_via_pubchem(monkeypatch):
    _pubchem_aspirin(monkeypatch)
    monkeypatch.setattr(chemtools, "molbloom_available", lambda: False)  # keep patent neutral
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
    assert profile["source"] == "pubchem"
    assert profile["patented"] is None  # molbloom disabled by stub
    assert "carboxylic acid" in profile["func_groups"]  # RDKit real
    assert profile["synthetic_accessibility"]["sa_score"] is not None


# ── lookup tier 4 ────────────────────────────────────────────────────────────


def test_lookup_chemical_tier4_pubchem(monkeypatch):
    from app.services import chemical_lookup

    _pubchem_aspirin(monkeypatch)
    monkeypatch.setattr(chemical_lookup, "_lookup_catalog", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_pubchem", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_lookup_offline_compounds", lambda q: None)
    monkeypatch.setattr(chemical_lookup, "_CACHE", {})
    hit = chemical_lookup.lookup_chemical("aspirin")
    assert hit["found"] is True
    assert hit["source"] == "pubchem"
    assert hit["smiles"] == "CC(=O)Oc1ccccc1C(=O)O"
    assert hit["cas"] == "50-78-2"


def test_availability_reflects_native_backends(monkeypatch):
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: True)
    monkeypatch.setattr(chemtools, "molbloom_available", lambda: True)
    caps = chemtools.availability()["capabilities"]
    assert caps["name_to_smiles"]["available"] is True
    assert caps["name_to_cas"]["available"] is True
    assert caps["explosive_check"]["available"] is True
    assert caps["patent_check"]["available"] is True


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
    assert "synthetic_accessibility" in data


def test_tools_status_endpoint():
    client = TestClient(app)
    resp = client.get("/api/chemical/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert "capabilities" in data
    assert data["enabled"] in (True, False)
    assert "rdkit_installed" in data
