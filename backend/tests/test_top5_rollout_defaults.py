"""Top-5 #1/#2/#3: default flag flips + search_deny retrieval contraction."""
from __future__ import annotations

from app.config import Settings
from app.domain.schemas import Evidence, ProductDomain
from app.domain.search_profiles import effective_search_deny, get_profile
from app.services.domain_tagging import (
    apply_domain_match,
    score_domain_match,
    search_deny_hits,
    search_deny_penalty,
)
from app.services.env_flags import FLAG_REGISTRY


def test_topicality_enforce_is_default():
    s = Settings()
    assert s.kb_relevance_shadow is False


def test_wiki_grayscale_product_defaults_on():
    s = Settings()
    assert s.wiki_project_dossier_enabled is True
    assert s.wiki_dossier_report_enabled is True
    assert s.wiki_chat_save_draft is True
    # Trust boundaries stay off.
    assert s.wiki_dossier_auto_patch is False
    assert s.wiki_dossier_llm_narrative is False
    assert s.auto_loop_on_sync is False
    assert s.auto_adopt_next_doe_on_loop is False


def test_kb_search_deny_flag_registered():
    attrs = {f.attr for f in FLAG_REGISTRY}
    assert "kb_search_deny_enabled" in attrs
    assert "kb_relevance_shadow" in attrs


def test_every_profile_has_search_deny():
    for domain in ProductDomain:
        p = get_profile(domain)
        assert p.search_deny, f"{domain} missing search_deny"
        assert "uranium" in " ".join(p.search_deny).lower() or "biomedical" in " ".join(
            p.search_deny
        ).lower()


def test_search_deny_marks_drift_none(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KB_SEARCH_DENY_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    ev = Evidence(
        source="openalex",
        identifier="deny-1",
        title="Uranium nuclear reactor coating study",
        snippet="aerospace turbine lithium-ion battery electrode",
        relevance=0.9,
    )
    match = score_domain_match(ev, ProductDomain.anticorrosion_coating)
    assert match == "none"
    apply_domain_match(ev, ProductDomain.anticorrosion_coating)
    assert ev.domain_match == "none"
    assert search_deny_hits(
        f"{ev.title} {ev.snippet}", get_profile(ProductDomain.anticorrosion_coating)
    ) >= 1
    assert search_deny_penalty(ev, ProductDomain.anticorrosion_coating) < 0
    get_settings.cache_clear()


def test_search_deny_can_be_disabled(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KB_SEARCH_DENY_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    ev = Evidence(
        source="openalex",
        identifier="deny-off",
        title="Uranium nuclear reactor coating",
        snippet="nuclear",
        relevance=0.9,
    )
    # Without retrieval deny, lexical allow may still be weak/none — penalty must be 0.
    assert search_deny_penalty(ev, ProductDomain.anticorrosion_coating) == 0.0
    get_settings.cache_clear()


def test_effective_search_deny_merges_negative_terms():
    p = get_profile(ProductDomain.anticorrosion_coating)
    merged = effective_search_deny(p, extra=["熔盐堆", "uranium"])
    assert any("uranium" in t.casefold() for t in merged)
    assert any("熔盐" in t for t in merged)
