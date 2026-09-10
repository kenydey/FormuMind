"""Source-policy wiring + ChemRxiv OpenAlex channel (arXiv removed)."""
from __future__ import annotations

from app.domain.schemas import ProductDomain, Requirement
from app.domain.search_profiles import CHEMRXIV_OPENALEX_SOURCE_ID
from app.services import literature


def test_build_streams_adds_chemrxiv_not_arxiv(monkeypatch):
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: type("S", (), {"openalex_enabled": True})(),
    )
    req = Requirement(domain=ProductDomain.surface_treatment, project_id="p1")
    streams = literature._build_streams(
        "passivation", "passivation magnesium", ["literature"], req, 30
    )
    names = [s["name"] for s in streams]
    assert "arxiv" not in names
    assert "chemrxiv" in names
    assert "openalex" in names
    assert not hasattr(literature, "search_arxiv")


def test_build_streams_chemrxiv_off(monkeypatch):
    from app.domain import search_profiles as sp

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: type("S", (), {"openalex_enabled": True})(),
    )
    base = sp.get_profile(ProductDomain.degreaser)
    off_policy = dict(base.source_policy)
    off_policy["chemrxiv"] = "off"
    monkeypatch.setattr(
        sp,
        "resolve_profile",
        lambda domain: sp.DomainSearchProfile(
            domain=base.domain,
            openalex_concept_ids=base.openalex_concept_ids,
            s2_fields_of_study=base.s2_fields_of_study,
            cpc_prefixes=base.cpc_prefixes,
            ipc_codes=base.ipc_codes,
            keyword_allow=base.keyword_allow,
            keyword_deny=base.keyword_deny,
            source_policy=off_policy,
            preferred_openalex_source_ids=base.preferred_openalex_source_ids,
            chemrxiv_openalex_source_id=base.chemrxiv_openalex_source_id,
        ),
    )
    req = Requirement(domain=ProductDomain.degreaser, project_id="p1")
    streams = literature._build_streams("degrease", "alkaline degreaser", ["literature"], req, 20)
    assert "chemrxiv" not in [s["name"] for s in streams]


def test_chemrxiv_source_id_constant():
    assert CHEMRXIV_OPENALEX_SOURCE_ID == "S4393918830"


def test_merge_drops_domain_match_none():
    from app.domain.schemas import Evidence

    rows = [
        Evidence(
            source="web",
            identifier="a",
            title="biomedical implant coating",
            snippet="orthopedic implant surfactant",
            relevance=0.9,
            domain_match="none",
        ),
        Evidence(
            source="OpenAlex",
            identifier="b",
            title="chrome-free passivation magnesium alloy",
            snippet="conversion coating AZ91",
            relevance=0.8,
            domain_match="strong",
            is_oa=True,
        ),
    ]
    kept, report = literature._merge_filter_rank(
        rows,
        "magnesium passivation",
        10,
        domain=ProductDomain.surface_treatment,
    )
    ids = {e.identifier for e in kept}
    assert "a" not in ids
    assert "b" in ids
    assert report.source_counts.get("OpenAlex", 0) >= 1
