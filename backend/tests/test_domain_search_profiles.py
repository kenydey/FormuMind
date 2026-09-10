"""Q0 unit tests — DomainSearchProfile mapping per ProductDomain."""
from __future__ import annotations

import pytest

from app.domain.schemas import ProductDomain
from app.domain.search_profiles import (
    DomainSearchProfile,
    all_profiles,
    arxiv_cat_clause,
    get_profile,
    openalex_concepts_filter,
    profile_for_domain_value,
)


REQUIRED_FIELDS = (
    "arxiv_categories",
    "openalex_concept_ids",
    "s2_fields_of_study",
    "cpc_prefixes",
    "ipc_codes",
    "keyword_allow",
    "keyword_deny",
    "source_policy",
)


@pytest.mark.parametrize("domain", list(ProductDomain))
def test_every_product_domain_has_profile(domain: ProductDomain):
    profile = get_profile(domain)
    assert isinstance(profile, DomainSearchProfile)
    assert profile.domain is domain
    for field in REQUIRED_FIELDS:
        value = getattr(profile, field)
        assert value, f"{domain.value}.{field} must be non-empty"


def test_get_profile_accepts_string_value():
    p = get_profile("surface_treatment")
    assert p.domain is ProductDomain.surface_treatment


def test_get_profile_unknown_raises():
    with pytest.raises((KeyError, ValueError)):
        get_profile("not_a_real_domain")


def test_all_profiles_covers_four_domains():
    profiles = all_profiles()
    assert len(profiles) == len(ProductDomain)
    assert {p.domain for p in profiles} == set(ProductDomain)


def test_profile_ipc_differs_by_domain():
    coating = get_profile(ProductDomain.anticorrosion_coating)
    degreaser = get_profile(ProductDomain.degreaser)
    surface = get_profile(ProductDomain.surface_treatment)
    autodep = get_profile(ProductDomain.autodeposition_coating)

    assert set(coating.cpc_prefixes) != set(degreaser.cpc_prefixes)
    assert set(coating.ipc_codes) != set(degreaser.ipc_codes)
    assert "C09D" in coating.cpc_prefixes
    assert "C23G" in degreaser.cpc_prefixes
    assert "C11D" in degreaser.cpc_prefixes
    assert "C23C" in surface.cpc_prefixes
    assert "C25D" in surface.cpc_prefixes or "C25D" in autodep.cpc_prefixes
    assert "C09D" in autodep.cpc_prefixes


def test_profile_arxiv_categories_present_and_differ():
    coating = get_profile(ProductDomain.anticorrosion_coating)
    surface = get_profile(ProductDomain.surface_treatment)
    assert "cond-mat.mtrl-sci" in coating.arxiv_categories
    assert "physics.chem-ph" in coating.arxiv_categories
    # surface_treatment adds applied-physics lean vs coating soft-matter lean
    assert "physics.app-ph" in surface.arxiv_categories
    assert coating.arxiv_categories != surface.arxiv_categories


def test_profile_arxiv_query_contains_cats():
    surface = get_profile(ProductDomain.surface_treatment)
    clause = arxiv_cat_clause(surface)
    assert clause.startswith("(") and clause.endswith(")")
    assert "cat:cond-mat.mtrl-sci" in clause
    assert " OR " in clause
    for cat in surface.arxiv_categories:
        assert f"cat:{cat}" in clause


def test_openalex_concepts_filter_format():
    coating = get_profile(ProductDomain.anticorrosion_coating)
    frag = openalex_concepts_filter(coating)
    assert frag.startswith("concepts.id:")
    assert "|" in frag
    for cid in coating.openalex_concept_ids:
        assert cid in frag
    # Resolved OpenAlex IDs (materials / corrosion / coating)
    assert "C192562407" in frag
    assert "C20625102" in frag


def test_s2_fields_exclude_medicine_for_coating():
    coating = get_profile(ProductDomain.anticorrosion_coating)
    fields = {f.lower() for f in coating.s2_fields_of_study}
    assert "medicine" not in fields
    assert "materials science" in fields
    assert "chemistry" in fields


def test_keyword_allow_and_deny_per_domain():
    coating = get_profile(ProductDomain.anticorrosion_coating)
    degreaser = get_profile(ProductDomain.degreaser)
    surface = get_profile(ProductDomain.surface_treatment)
    autodep = get_profile(ProductDomain.autodeposition_coating)

    assert any("防腐" in k or "corrosi" in k.lower() for k in coating.keyword_allow)
    assert any("脱脂" in k or "degreas" in k.lower() for k in degreaser.keyword_allow)
    assert any("钝化" in k or "passivat" in k.lower() for k in surface.keyword_allow)
    assert any(
        "自沉积" in k or "autodeposition" in k.lower() or "autophoretic" in k.lower()
        for k in autodep.keyword_allow
    )

    for p in (coating, degreaser, surface, autodep):
        deny_l = " ".join(p.keyword_deny).lower()
        assert "biomedical" in deny_l or "implant" in deny_l


def test_source_policy_keys():
    p = get_profile(ProductDomain.degreaser)
    assert p.source_policy["arxiv"] == "off"
    assert p.source_policy["chemrxiv"] == "primary"
    assert p.source_policy["google_scholar"] == "support"
    assert p.source_policy["openalex"] in {"primary", "support"}
    assert p.chemrxiv_openalex_source_id == "S4393918830"
    coating = get_profile(ProductDomain.anticorrosion_coating)
    assert coating.preferred_openalex_source_ids
    assert "S41155759" in coating.preferred_openalex_source_ids


def test_policy_helpers_respect_tiers():
    from app.domain.search_profiles import policy_allows, policy_page_size, resolve_profile

    p = resolve_profile(ProductDomain.surface_treatment)
    assert policy_allows(p, "chemrxiv") is True
    assert policy_allows(p, "arxiv") is False
    assert policy_page_size(p, "arxiv", 15) == 0
    assert policy_page_size(p, "semantic_scholar", 30) == max(1, int(30 * 0.33))
    assert policy_page_size(p, "openalex", 30) == 30


def test_profile_for_domain_value_cached():
    a = profile_for_domain_value("degreaser")
    b = profile_for_domain_value("degreaser")
    assert a is b
    assert a.domain is ProductDomain.degreaser
