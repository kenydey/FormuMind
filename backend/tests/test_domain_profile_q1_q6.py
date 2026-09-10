"""P0 DomainSearchProfile Q1–Q6 acceptance tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings, get_settings
from app.domain.schemas import Evidence, ProductDomain
from app.domain.search_profiles import (
    cpc_query_clause,
    get_profile,
    openalex_concepts_filter,
)
from app.services.deep_research.query_expander import prepare_search_queries
from app.services.domain_tagging import domain_match_bonus, tag_evidence_domain
from app.services import kb_ingest


@pytest.fixture(autouse=True)
def _clear_settings(monkeypatch):
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()



def test_profile_ipc_differs_by_domain():
    deg = prepare_search_queries("脱脂清洗", domain=ProductDomain.degreaser)
    coat = prepare_search_queries("防腐涂料", domain=ProductDomain.anticorrosion_coating)
    assert deg.ipc_codes != coat.ipc_codes
    assert any(c.startswith("C23G") or c.startswith("C11D") for c in deg.ipc_codes)
    assert any(c.startswith("C09D") for c in coat.ipc_codes)



def test_openalex_filter_param(monkeypatch):
    from app.services import search_providers as sp

    captured = {}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            captured["params"] = params
            return FakeResp()

    monkeypatch.setattr(sp.httpx, "Client", FakeClient)
    settings = Settings(openalex_enabled=True, openalex_concept_filter=True, domain_profile_search=True)
    sp.search_openalex("passivation", limit=1, settings=settings, domain=ProductDomain.surface_treatment)
    assert "filter" in (captured.get("params") or {})
    assert "concepts.id:" in captured["params"]["filter"]


def test_patent_cpc_appended(monkeypatch):
    from app.services import search_providers as sp

    captured = {}

    def fake_search(engine, q, key, limit, offset, extra_params=None):
        captured["q"] = q
        return {"organic_results": []}

    monkeypatch.setattr(sp, "_serpapi_search", fake_search)
    monkeypatch.setattr(sp, "effective_setting", lambda s, k: "fake-key")
    settings = Settings(patent_cpc_filter=True, domain_profile_search=True)
    sp.search_serpapi_patents("alkaline cleaner", limit=1, settings=settings, domain=ProductDomain.degreaser)
    assert "CPC=" in captured["q"]
    assert "C23G" in captured["q"] or "C11D" in captured["q"]


def test_evidence_domain_match_scoring():
    e = Evidence(
        source="arXiv",
        identifier="x",
        title="epoxy anticorrosion coating",
        snippet="salt spray",
        relevance=0.5,
    )
    tag_evidence_domain(e, ProductDomain.anticorrosion_coating, taxonomy_source="arxiv", match="strong")
    assert e.domain_match == "strong"
    assert "anticorrosion_coating" in e.domain_tags
    assert domain_match_bonus(e) > 0
    e2 = Evidence(
        source="web",
        identifier="y",
        title="quasar astronomy",
        snippet="interstellar",
        relevance=0.5,
    )
    tag_evidence_domain(e2, ProductDomain.anticorrosion_coating, taxonomy_source="lexical", match="none")
    assert domain_match_bonus(e2) < 0


def test_ingest_skips_low_relevance(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KB_INGEST_MIN_RELEVANCE", "0.45")
    get_settings.cache_clear()
    assert get_settings().kb_ingest_min_relevance == 0.45
    lows = [
        Evidence(source="arXiv", identifier="a1", title="epoxy coating", snippet="corrosion", relevance=0.2),
        Evidence(source="arXiv", identifier="a2", title="epoxy coating resin", snippet="passivation", relevance=0.9),
    ]
    monkeypatch.setattr(kb_ingest, "topic_gate", lambda *a, **k: True)

    def _classify(ev):
        return "literature"

    # Patch whatever classify path select uses
    with patch.object(kb_ingest, "topic_gate", lambda *a, **k: True):
        # inject classify via fulltext module used inside
        import app.services.kb_ingest as ki

        # Read source to find classify import — monkeypatch after import inside function
        original = ki.select_ingest_targets

        def wrapped(evidence, **kwargs):
            kwargs.setdefault("skip_topic_filter", True)
            kwargs.setdefault("write_audit", False)
            # Patch classify in the namespace the function will import
            with patch("app.services.fulltext_fetcher.classify", _classify):
                with patch("app.services.fulltext_fetcher.classify", _classify):
                    try:
                        return original(evidence, **kwargs)
                    except Exception:
                        # Fallback: classify by patching ff used locally
                        with patch.dict("sys.modules", {"app.services.fulltext_fetcher": MagicMock(classify=_classify)}):
                            return original(evidence, **kwargs)

        out = wrapped(lows, domain=ProductDomain.anticorrosion_coating)
    ids = [e.identifier for e, _ in out]
    assert "a1" not in ids
    assert "a2" in ids


def test_ingest_patent_needs_domain_or_cpc(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KB_INGEST_PATENT_EXEMPT", "false")
    get_settings.cache_clear()
    assert get_settings().kb_ingest_patent_exempt is False
    assert (
        kb_ingest.topic_gate(
            "A method of manufacturing a turbine blade",
            kind="patent",
            domain=ProductDomain.surface_treatment,
        )
        is False
    )
    assert (
        kb_ingest.topic_gate(
            "Chrome-free conversion coating C23C for aluminum passivation",
            kind="patent",
            domain=ProductDomain.surface_treatment,
        )
        is True
    )


def test_deny_keyword_blocks():
    assert (
        kb_ingest.topic_gate(
            "biomedical implant orthopedic stent coating",
            kind="literature",
            domain=ProductDomain.anticorrosion_coating,
        )
        is False
    )


def test_audit_row_written(monkeypatch):
    from app.services import kb_ingest_audit as audit

    rows = []
    monkeypatch.setattr(audit, "_try_sqlite", lambda row: False)
    monkeypatch.setattr(audit, "_append_jsonl", lambda row: rows.append(row))
    audit.record_ingest_audit(
        project_id="p1",
        domain="surface_treatment",
        query="钝化 720",
        evidence_id="x",
        source="arXiv",
        action="skip",
        reason="topic_miss",
        domain_match="none",
    )
    assert rows and rows[0]["action"] == "skip"
    assert rows[0]["query_fingerprint"]


def test_env_flags_include_profile_toggles():
    from app.services.env_flags import FLAG_REGISTRY

    names = {f.attr for f in FLAG_REGISTRY}
    assert "domain_profile_search" in names
    assert "openalex_concept_filter" in names
    assert "patent_cpc_filter" in names
    assert "kb_ingest_patent_exempt" in names


def test_default_min_relevance_is_045():
    get_settings.cache_clear()
    # Fresh Settings defaults (ignore env if CI sets it)
    s = Settings()
    # env may override; assert field default on model
    field = Settings.model_fields["kb_ingest_min_relevance"]
    assert field.default == 0.45
