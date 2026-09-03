"""Phase B tests — chemical entity query expansion, chemlit evidence
splitting, and requirement material enrichment (all ChemCrow-gateway backed,
all no-ops when chemcrow is absent)."""
from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import chemtools
from app.services.deep_research.models import ExpandedQuery
from app.services.deep_research.query_expander import (
    _augment_with_chemical_entities,
    prepare_search_queries,
)
from app.services.literature import split_chemcrow_answer


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


# ── query expansion chemical normalization ───────────────────────────────────


def test_augment_is_noop_without_chemcrow():
    expanded = ExpandedQuery(
        intent="x",
        chinese_keywords=["防腐涂层"],
        english_synonyms=["isophorone diamine", "epoxy coating"],
        ipc_cpc_suggestions=["C09D"],
    )
    out = _augment_with_chemical_entities(expanded)
    assert out is expanded  # untouched object, zero behaviour change


def test_augment_appends_cas_numbers(monkeypatch):
    def fake_cas(arg):
        return {"isophorone diamine": "CAS: 2855-13-2"}.get(arg, "not found")

    _install_fake_chemcrow(monkeypatch, Query2CAS=fake_cas)
    expanded = ExpandedQuery(
        intent="x",
        chinese_keywords=[],
        english_synonyms=["isophorone diamine", "corrosion protection mechanism"],
        ipc_cpc_suggestions=[],
    )
    out = _augment_with_chemical_entities(expanded)
    assert "2855-13-2" in out.english_synonyms
    # original synonyms preserved in order
    assert out.english_synonyms[0] == "isophorone diamine"


def test_augment_skips_long_phrases_and_dedups(monkeypatch):
    calls: list[str] = []

    def fake_cas(arg):
        calls.append(arg)
        return "CAS: 2855-13-2"

    _install_fake_chemcrow(monkeypatch, Query2CAS=fake_cas)
    expanded = ExpandedQuery(
        intent="x",
        chinese_keywords=[],
        english_synonyms=[
            "IPDA",
            "a very long phrase that is not a chemical entity at all",
            "2855-13-2",  # already a CAS-looking synonym
        ],
        ipc_cpc_suggestions=[],
    )
    out = _augment_with_chemical_entities(expanded)
    # long phrase (>4 words) never queried
    assert all(len(c.split()) <= 4 for c in calls)
    # resolved CAS equals an existing synonym -> not duplicated
    assert out.english_synonyms.count("2855-13-2") == 1


def test_prepare_search_queries_includes_cas_in_patent_query(monkeypatch):
    _install_fake_chemcrow(monkeypatch, Query2CAS="2855-13-2")
    sq = prepare_search_queries("isophorone diamine coating")
    # offline expansion tokens + appended CAS should reach the patent query
    assert "2855-13-2" in sq.patent_q or "2855-13-2" in sq.rank_q


# ── chemlit answer splitting ─────────────────────────────────────────────────


def test_split_no_doi_keeps_legacy_single_blob():
    out = split_chemcrow_answer("An answer with no citations.", query="epoxy")
    assert len(out) == 1
    assert out[0].source == "ChemCrow-Lit"
    assert out[0].identifier.startswith("chemlit:")
    assert out[0].relevance == 0.92


def test_split_extracts_doi_citations():
    text = (
        "Epoxy-amine coatings resist salt spray (Smith2020).\n"
        "References:\n"
        "1. Smith et al., Prog. Org. Coat. 2020. 10.1016/j.porgcoat.2020.105678\n"
        "2. Lee et al., Corros. Sci. 2021. 10.1016/j.corsci.2021.109432\n"
    )
    out = split_chemcrow_answer(text, query="epoxy salt spray")
    ids = [e.identifier for e in out]
    assert ids[0].startswith("chemlit:")
    assert "doi:10.1016/j.porgcoat.2020.105678" in ids
    assert "doi:10.1016/j.corsci.2021.109432" in ids
    # citation rows rank slightly below the synthesized answer
    assert all(e.relevance < 0.92 for e in out[1:])
    # duplicate DOIs collapse
    out2 = split_chemcrow_answer(text + text, query="epoxy salt spray")
    assert len([i for i in (e.identifier for e in out2) if i.startswith("doi:")]) == 2


def test_split_respects_limit():
    text = "\n".join(f"ref {i}: 10.1000/test.{i}" for i in range(10))
    out = split_chemcrow_answer(text, query="q", limit=3)
    assert len(out) == 4  # 1 answer + 3 citations


def test_split_empty_returns_empty():
    assert split_chemcrow_answer("", query="q") == []


# ── material enrichment ──────────────────────────────────────────────────────


def test_enrich_materials_catalog_fill_works_offline():
    from app.domain.schemas import MaterialSpec

    mats = [MaterialSpec(name="Isophorone diamine (IPDA)", role="hardener", weight_pct=5.0)]
    warnings = chemtools.enrich_material_specs(mats)
    assert mats[0].smiles  # curated catalog has a SMILES for IPDA
    assert warnings == []


def test_enrich_materials_gateway_fill_and_screen(monkeypatch):
    from app.domain.schemas import MaterialSpec

    _install_fake_chemcrow(
        monkeypatch,
        Query2SMILES="CCO",
        ControlChemCheck="This molecule appears in a list of controlled chemicals",
    )
    mats = [MaterialSpec(name="mystery solvent X", role="solvent", weight_pct=10.0)]
    warnings = chemtools.enrich_material_specs(mats)
    assert mats[0].smiles == "CCO"
    assert warnings and "管制" in warnings[0]


def test_enrich_materials_noop_without_chemcrow():
    from app.domain.schemas import MaterialSpec

    mats = [MaterialSpec(name="totally unknown compound", role="additive")]
    warnings = chemtools.enrich_material_specs(mats)
    assert mats[0].smiles is None
    assert warnings == []


def test_parse_intent_result_has_warnings_field():
    from app.services.intent import parse_intent

    result = parse_intent("镀锌板防腐涂层，盐雾 500 小时")
    assert isinstance(result.warnings, list)


def test_enrich_materials_endpoint():
    client = TestClient(app)
    resp = client.post(
        "/api/chemical/enrich-materials",
        json={"materials": [{"name": "Xylene", "role": "solvent", "weight_pct": 5}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["materials"][0]["smiles"]
    assert data["warnings"] == []
