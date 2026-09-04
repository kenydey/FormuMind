"""Phase B tests — chemical entity query expansion, chemlit evidence
splitting, and requirement material enrichment (native PubChem/RDKit gateway
backed, all no-ops when the backends are absent)."""
from __future__ import annotations

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
from app.services.literature import split_lit_answer


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    chemtools.clear_cache()
    yield
    get_settings.cache_clear()
    chemtools.clear_cache()


def _stub_pubchem(monkeypatch, *, cas_by_name: dict[str, str] | None = None, spy: list | None = None):
    """Route PubChem synonym lookups to canned CAS answers (no network)."""
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: True)

    def fake_get(path: str):
        if spy is not None:
            spy.append(path)
        if "/synonyms/" not in path:
            return None
        for name, cas in (cas_by_name or {}).items():
            if name.lower().replace(" ", "%20") in path or name in path:
                return {"InformationList": {"Information": [{"Synonym": [name, cas]}]}}
        return None

    monkeypatch.setattr(chemtools, "_pubchem_get", fake_get)


# ── query expansion chemical normalization ───────────────────────────────────


def test_augment_is_noop_offline(monkeypatch):
    monkeypatch.setattr(chemtools, "pubchem_available", lambda: False)
    expanded = ExpandedQuery(
        intent="x",
        chinese_keywords=["防腐涂层"],
        english_synonyms=["isophorone diamine", "epoxy coating"],
        ipc_cpc_suggestions=["C09D"],
    )
    out = _augment_with_chemical_entities(expanded)
    assert out is expanded  # untouched object, zero behaviour change


def test_augment_appends_cas_numbers(monkeypatch):
    _stub_pubchem(monkeypatch, cas_by_name={"isophorone diamine": "2855-13-2"})
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
    _stub_pubchem(monkeypatch, cas_by_name={"IPDA": "2855-13-2", "isophorone diamine": "2855-13-2"}, spy=calls)
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
    from app.services.deep_research import query_expander as qe_mod
    from app.services.deep_research.models import ExpandedQuery

    _stub_pubchem(monkeypatch, cas_by_name={"isophorone diamine": "2855-13-2"})
    # Pin the LLM expand step (env-dependent) so the test targets the CAS
    # augmentation reaching the patent/rank queries deterministically.
    monkeypatch.setattr(
        qe_mod.QueryExpander,
        "expand",
        lambda self, q: ExpandedQuery(
            intent="x",
            chinese_keywords=[],
            english_synonyms=["isophorone diamine"],
            ipc_cpc_suggestions=[],
        ),
    )
    sq = prepare_search_queries("isophorone diamine coating")
    # offline expansion tokens + appended CAS should reach the patent query
    assert "2855-13-2" in sq.patent_q or "2855-13-2" in sq.rank_q


# ── chemlit answer splitting ─────────────────────────────────────────────────


def test_split_no_doi_keeps_legacy_single_blob():
    out = split_lit_answer("An answer with no citations.", query="epoxy")
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
    out = split_lit_answer(text, query="epoxy salt spray")
    ids = [e.identifier for e in out]
    assert ids[0].startswith("chemlit:")
    assert "doi:10.1016/j.porgcoat.2020.105678" in ids
    assert "doi:10.1016/j.corsci.2021.109432" in ids
    # citation rows rank slightly below the synthesized answer
    assert all(e.relevance < 0.92 for e in out[1:])
    # duplicate DOIs collapse
    out2 = split_lit_answer(text + text, query="epoxy salt spray")
    assert len([i for i in (e.identifier for e in out2) if i.startswith("doi:")]) == 2


def test_split_respects_limit():
    text = "\n".join(f"ref {i}: 10.1000/test.{i}" for i in range(10))
    out = split_lit_answer(text, query="q", limit=3)
    assert len(out) == 4  # 1 answer + 3 citations


def test_split_empty_returns_empty():
    assert split_lit_answer("", query="q") == []


# ── material enrichment ──────────────────────────────────────────────────────


def test_enrich_materials_catalog_fill_works_offline():
    from app.domain.schemas import MaterialSpec

    mats = [MaterialSpec(name="Isophorone diamine (IPDA)", role="hardener", weight_pct=5.0)]
    warnings = chemtools.enrich_material_specs(mats)
    assert mats[0].smiles  # curated catalog has a SMILES for IPDA
    assert warnings == []


def test_enrich_materials_gateway_fill_via_pubchem(monkeypatch):
    from app.domain.schemas import MaterialSpec

    monkeypatch.setattr(chemtools, "pubchem_available", lambda: True)
    monkeypatch.setattr(
        chemtools,
        "_pubchem_get",
        lambda path: {"PropertyTable": {"Properties": [{"SMILES": "CCO"}]}}
        if "/property/" in path
        else None,
    )
    mats = [MaterialSpec(name="mystery solvent X", role="solvent", weight_pct=10.0)]
    warnings = chemtools.enrich_material_specs(mats)
    assert mats[0].smiles == "CCO"
    # controlled check is neutral post-ChemCrow → no compliance warning
    assert warnings == []


def test_enrich_materials_noop_offline(monkeypatch):
    from app.domain.schemas import MaterialSpec

    monkeypatch.setattr(chemtools, "pubchem_available", lambda: False)
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
