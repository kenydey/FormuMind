"""Phase A search query differentiation and merge/rank filtering."""
from __future__ import annotations

from app.domain.schemas import Evidence
from app.services.deep_research.models import ExpandedQuery
from app.services.deep_research.query_expander import (
    build_chinese_query,
    build_patent_query,
    build_rank_query,
    build_western_query,
    prepare_search_queries,
)
from app.services import literature


def _expanded() -> ExpandedQuery:
    return ExpandedQuery(
        intent="水性防腐涂料",
        chinese_keywords=["水性", "防腐涂料", "环氧底漆"],
        english_synonyms=["waterborne", "anticorrosive coating", "epoxy primer"],
        ipc_cpc_suggestions=["C09D", "C09D175/04", "C08G18/00"],
    )


def test_build_rank_query_duplicates_ipc_for_weight():
    eq = _expanded()
    q = build_rank_query(eq, "zinc phosphate")
    assert q.count("C09D") == 2
    assert "waterborne" in q
    assert "水性" in q


def test_build_patent_query_english_heavy():
    eq = _expanded()
    q = build_patent_query(eq, "zinc phosphate epoxy")
    assert "zinc phosphate epoxy" in q
    assert "waterborne" in q
    # Chinese keywords should not dominate patent full-text query
    assert "防腐涂料" not in q or "anticorrosive" in q


def test_build_western_and_chinese_queries():
    eq = _expanded()
    assert "waterborne" in build_western_query(eq, "fallback topic")
    assert "水性" in build_chinese_query(eq, "")


def test_prepare_search_queries_offline():
    sq = prepare_search_queries("水性聚氨酯防腐涂料")
    assert sq.rank_q
    assert sq.patent_q
    assert sq.expanded.chinese_keywords
    assert sq.ipc_codes


def test_merge_filter_drops_irrelevant_literature():
    q = "zinc phosphate epoxy primer"
    relevant = Evidence(
        source="arXiv",
        identifier="arxiv:1",
        title="Zinc phosphate epoxy anticorrosive primer",
        snippet="corrosion protection",
        relevance=0.8,
    )
    junk = Evidence(
        source="arXiv",
        identifier="arxiv:2",
        title="Quantum computing advances",
        snippet="unrelated physics",
        relevance=0.9,
    )
    merged, _ = literature._merge_filter_rank([junk, relevant], q, 10)
    ids = {e.identifier for e in merged}
    assert "arxiv:1" in ids
    assert "arxiv:2" not in ids


# ── narrow-venue boolean queries (2026-09-11) ────────────────────────────────
# OpenAlex ANDs bare space-separated words and stems them, so the expanded keyword
# string that works over the whole corpus collapses inside a single repository:
# measured against ChemRxiv, the expanded string returned 1 hit where a grouped
# boolean returned 1,057. These pin the shape of that query.


def _req(substrate="magnesium_alloy"):
    from app.domain.schemas import ProductDomain, Requirement, Substrate

    return Requirement(domain=ProductDomain.surface_treatment, substrate=Substrate(substrate))


def test_venue_scoped_query_groups_substrate_against_process():
    from app.domain.search_profiles import get_profile
    from app.services.search_providers import venue_scoped_query

    q = venue_scoped_query(
        ["magnesium alloy", "passivation"],
        req=_req(),
        profile=get_profile("surface_treatment"),
    )
    assert " AND " in q, "substrate must be intersected with the process group"
    assert "magnesium" in q
    assert "passivation" in q
    # Parenthesised OR groups, unquoted: quoting makes a phrase match as a unit,
    # which *tightens* the query and re-starves the venue.
    assert q.startswith("(")
    assert '"' not in q


def test_venue_scoped_query_drops_phrases_and_non_ascii():
    """Multi-word and CJK entries cannot anchor a venue query — OpenAlex would
    AND the words of a phrase, and ChemRxiv indexes no Chinese."""
    from app.domain.search_profiles import get_profile
    from app.services.search_providers import venue_scoped_query

    q = venue_scoped_query(
        ["ignored"],  # fallback unused when structured context exists
        req=_req(),
        profile=get_profile("surface_treatment"),
    )
    for token in ("magnesium alloy", "镁合金", "镁材钝化"):
        assert token not in q


def test_venue_scoped_query_uses_only_the_domain_vocabulary():
    from app.domain.search_profiles import get_profile
    from app.services.search_providers import venue_scoped_query

    q = venue_scoped_query([], req=_req(), profile=get_profile("surface_treatment"))
    assert "passivation" in q
    # A degreaser word must not leak into a surface-treatment query.
    assert "degreasing" not in q


def test_venue_scoped_query_keeps_allow_stems_out():
    """`keyword_allow` holds stems for Python substring matching; querying for
    them returned 5 unrelated papers, which is why `venue_terms` exists."""
    from app.domain.search_profiles import get_profile
    from app.services.search_providers import venue_scoped_query

    q = venue_scoped_query([], req=_req(), profile=get_profile("surface_treatment"))
    # Compare as *terms*, not substrings — "passivation" legitimately contains
    # "passivat"; what must not appear is the bare stem as its own query word.
    import re as _re

    tokens = _re.findall(r"[A-Za-z0-9]+", q)
    for stem in ("passivat", "phosphat", "anodiz"):
        assert stem not in tokens
    assert "passivation" in tokens


def test_venue_scoped_query_falls_back_without_context():
    """No requirement / profile ⇒ previous behaviour, not an empty query."""
    from app.services.search_providers import venue_scoped_query

    assert venue_scoped_query(["a", "b"], req=None, profile=None) == "a b"
    assert venue_scoped_query("verbatim query", req=None, profile=None) == "verbatim query"


def test_venue_scoped_query_substrate_only_when_profile_missing():
    from app.services.search_providers import venue_scoped_query

    q = venue_scoped_query([], req=_req(), profile=None)
    assert q == "(magnesium OR AZ91 OR AZ31 OR AM60)"
