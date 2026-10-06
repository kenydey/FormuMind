"""The patent search runs on EPO OPS now (``search_providers.search_epo_patents``), and ``patent-client`` is gone.

Offline throughout: the provider is handed a mock-transport client, so what is pinned is how a search turns into OPS
requests and how OPS hits turn into evidence - not the live service (see ``test_epo_ops.py`` for what has and has not
been verified against it).
"""
from __future__ import annotations

import ast
import logging
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import epo_ops, literature, provider_health, search_providers
from tests.test_epo_ops import CN_DOC, EP_DOC, US_DOC, FakeOps, _payload

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"


@pytest.fixture(autouse=True)
def _clean_state():
    epo_ops.forget_tokens()
    provider_health.reset_provider_health()
    yield
    epo_ops.forget_tokens()
    provider_health.reset_provider_health()


@pytest.fixture()
def keyed(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", "provider-key", raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", "provider-secret-9876543210", raising=False)
    return settings


@pytest.fixture()
def ops():
    return FakeOps()


def _queries(ops: FakeOps) -> list[str]:
    return [parse_qs(urlparse(str(r.url)).query)["q"][0] for r in ops.searches()]


def _search(ops: FakeOps, query="magnesium alloy passivation", limit=5, offset=0, **kwargs):
    with ops.client() as client:
        return search_providers.search_epo_patents(query, limit, offset, client=client, **kwargs)


# ── evidence ─────────────────────────────────────────────────────────────────


def test_without_credentials_nothing_is_asked_and_nothing_is_returned(ops, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", None, raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", None, raising=False)
    assert _search(ops) == []
    assert not ops.requests


def test_one_key_without_the_secret_is_not_enough(ops, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", "only-a-key", raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", None, raising=False)
    assert _search(ops) == []
    assert not ops.requests


def test_hits_become_evidence_with_the_publication_number_as_identifier(ops, keyed):
    ops.search_replies = [httpx.Response(200, json=_payload(US_DOC, EP_DOC, CN_DOC))]
    out = _search(ops, limit=5)
    assert [e.identifier for e in out] == ["US2020123456A1", "EP3211048A1", "CN104789083B"]
    first = out[0]
    assert isinstance(first, Evidence) and first.source == "EPO"
    assert first.title == "Chromium-free passivation of magnesium"
    assert first.snippet == "A passivation bath. It contains no chromium."
    assert first.assignee == "ACME CORP" and first.pub_date == "2020-04-23" and first.pub_year == 2020
    assert first.is_seed_corpus is False
    assert [e.relevance for e in out] == sorted((e.relevance for e in out), reverse=True)
    assert all(0 <= e.relevance <= 1 for e in out)
    # a hit with no abstract still has something to show; one with no applicant has none
    cn = out[2]
    assert cn.snippet == "Passivation solution" and cn.assignee is None


def test_the_snippet_is_capped(ops, keyed):
    long_doc = {**US_DOC, "abstract": {"@lang": "en", "p": {"$": "x" * 900}}}
    ops.search_replies = [httpx.Response(200, json=_payload(long_doc))]
    assert len(_search(ops)[0].snippet) == 400


def test_the_identifiers_are_ones_the_rest_of_the_app_understands(ops, keyed):
    """Dedup in ``merge_patent_evidence`` and the KB's ``origin_url`` aliasing both key on the normalised number."""
    from app.services.patent_ids import normalize_patent_pub

    ops.search_replies = [httpx.Response(200, json=_payload(US_DOC, EP_DOC, CN_DOC))]
    for evidence in _search(ops):
        office, compact = normalize_patent_pub(evidence.identifier)
        assert office == evidence.identifier[:2] and compact == evidence.identifier


def test_offset_and_limit_slice_the_ranked_hits(ops, keyed):
    ops.search_replies = [httpx.Response(200, json=_payload(US_DOC, EP_DOC, CN_DOC))]
    out = _search(ops, limit=1, offset=1)
    assert [e.identifier for e in out] == ["EP3211048A1"]
    assert out[0].relevance == pytest.approx(0.98)  # its rank in the whole result, not in the slice


# ── requests ─────────────────────────────────────────────────────────────────


def test_the_query_is_a_conjunction_of_its_words_and_the_cpc_classes_are_added(ops, keyed):
    _search(ops, "zinc phosphate epoxy primer", cpc_codes=["C23C22"])
    assert _queries(ops) == ['ta=zinc and ta=phosphate and ta=epoxy and ta=primer and (cpc="C23C22")']


def test_a_search_that_finds_nothing_is_retried_once_with_fewer_words(ops, keyed):
    ops.search_replies = [httpx.Response(404, text="<error/>"), httpx.Response(200, json=_payload(US_DOC))]
    out = _search(ops, "chromium free passivation magnesium alloy corrosion resistance")
    assert [e.identifier for e in out] == ["US2020123456A1"]
    first, second = _queries(ops)
    assert first.count("ta=") == 6 and second == "ta=chromium and ta=free and ta=passivation"


def test_a_short_query_is_not_retried_with_itself(ops, keyed):
    ops.search_replies = [httpx.Response(404, text="<error/>")]
    assert _search(ops, "zinc primer") == []
    assert len(ops.searches()) == 1


def test_a_query_with_nothing_to_search_for_makes_no_request(ops, keyed):
    assert _search(ops, "镁合金无铬钝化") == []
    assert not ops.requests


def test_more_than_one_page_is_fetched_in_ops_sized_windows(ops, keyed):
    first = [{**US_DOC, "@doc-number": str(1000 + i), "bibliographic-data": {**US_DOC["bibliographic-data"], "publication-reference": {"document-id": {"@document-id-type": "docdb", "country": {"$": "US"}, "doc-number": {"$": str(1000 + i)}, "kind": {"$": "A1"}}}}} for i in range(25)]
    second = [{**EP_DOC, "bibliographic-data": {**EP_DOC["bibliographic-data"], "publication-reference": {"document-id": {"@document-id-type": "docdb", "country": {"$": "EP"}, "doc-number": {"$": str(5000 + i)}, "kind": {"$": "A1"}}}}} for i in range(5)]
    ops.search_replies = [
        httpx.Response(200, json=_payload(*first, total=30)),
        httpx.Response(200, json=_payload(*second, total=30)),
    ]
    out = _search(ops, limit=30)
    assert len(out) == 30
    assert [r.headers["x-ops-range"] for r in ops.searches()] == ["1-25", "26-30"]
    assert len({e.identifier for e in out}) == 30


def test_a_window_is_not_larger_than_what_was_asked_for(ops, keyed):
    _search(ops, limit=3)
    assert [r.headers["x-ops-range"] for r in ops.searches()] == ["1-3"]


# ── failure ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(403, text="<fault/>", headers={"X-Rejection-Reason": "RegisteredQuotaPerWeek"}),
        httpx.Response(400, text="<error><message>CLIENT.InvalidQuery</message></error>"),
        httpx.Response(503, text="down"),
        httpx.Response(200, text="not json"),
        httpx.ConnectError("no route"),
    ],
)
def test_every_failure_is_an_empty_list_that_the_other_sources_cover(ops, keyed, reply, caplog):
    ops.search_replies = [reply]
    with caplog.at_level(logging.WARNING, logger=search_providers.logger.name):
        assert _search(ops) == []
    assert any("EPO OPS patent search failed" in r.getMessage() for r in caplog.records)
    assert "provider-secret-9876543210" not in caplog.text


def test_refused_credentials_are_reported_not_swallowed_silently(ops, keyed, caplog):
    ops.token_status = 401
    with caplog.at_level(logging.WARNING, logger=search_providers.logger.name):
        assert _search(ops) == []
    assert "ClientId is Invalid" in caplog.text
    assert "provider-secret-9876543210" not in caplog.text and "provider-key" not in caplog.text


def test_repeated_failures_open_the_breaker_and_stop_the_requests(ops, keyed):
    threshold = int(getattr(get_settings(), "provider_breaker_threshold", 5) or 5)
    ops.token_status = 401
    for _ in range(threshold):
        assert _search(ops) == []
    before = len(ops.requests)
    assert _search(ops) == []
    assert len(ops.requests) == before, "an open breaker must not keep calling OPS"
    assert any(e["provider"] == "epo_ops" and e["outcome"] == "breaker_open_skip" for e in provider_health.recent_provider_events())


def test_a_success_is_recorded_and_resets_the_failure_count(ops, keyed):
    assert _search(ops)
    events = provider_health.recent_provider_events()
    assert any(e["provider"] == "epo_ops" and e["outcome"] == "ok" for e in events)


# ── through the public search ────────────────────────────────────────────────


def test_search_patents_by_query_puts_ops_hits_ahead_of_the_seed_corpus(ops, keyed, monkeypatch):
    ops.search_replies = [httpx.Response(200, json=_payload(US_DOC, EP_DOC))]
    real = search_providers.search_epo_patents

    def with_client(query, limit=5, offset=0, **kwargs):
        with ops.client() as client:
            return real(query, limit, offset, client=client, **kwargs)

    monkeypatch.setattr(search_providers, "search_epo_patents", with_client)
    out = literature.search_patents_by_query("magnesium passivation", limit=2)
    assert [e.identifier for e in out] == ["US2020123456A1", "EP3211048A1"]
    assert not any(e.is_seed_corpus for e in out)


def test_the_ipc_codes_of_a_search_reach_ops_as_cpc(ops, keyed, monkeypatch):
    real = search_providers.search_epo_patents

    def with_client(query, limit=5, offset=0, **kwargs):
        with ops.client() as client:
            return real(query, limit, offset, client=client, **kwargs)

    monkeypatch.setattr(search_providers, "search_epo_patents", with_client)
    literature.search_patents_by_query("zinc", limit=2, ipc_codes=["C23C22", "C09D5/08"])
    assert _queries(ops) == ['ta=zinc and (cpc="C23C22" or cpc="C09D5/08")']


def test_without_credentials_the_public_search_still_answers_from_the_seed_corpus(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", None, raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", None, raising=False)
    monkeypatch.setattr(settings, "serpapi_api_key", None, raising=False)
    out = literature.search_patents_by_query("zinc epoxy", limit=3)
    assert out and all(e.is_seed_corpus for e in out)


# ── the status says what the search can do ───────────────────────────────────


def test_credentials_alone_make_the_official_patent_search_available(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", "key", raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", "secret", raising=False)
    status = literature.get_source_availability()
    assert status["epo"]["available"] is True and status["epo"]["reason"] is None and status["epo"]["hint"] is None
    assert status["patents"]["reason"] is None and status["patents"]["hint"] is None


def test_without_credentials_the_hint_is_about_the_keys_and_nothing_to_install(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", None, raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", None, raising=False)
    status = literature.get_source_availability()
    assert status["epo"]["available"] is False and status["epo"]["reason"] == "key_missing"
    assert "EPO_CONSUMER" in status["epo"]["hint"]
    patents = status["patents"]
    assert patents["available"] is True and patents["offline_fallback"] is True and patents["reason"] == "offline_seed"
    assert "EPO" in patents["hint"] and "developers.epo.org" in patents["hint"]
    for text in (patents["hint"], status["epo"]["hint"]):
        assert "pip install" not in text and "patent-client" not in text and ".[patents]" not in text


def test_the_settings_page_test_asks_ops_for_a_token(monkeypatch):
    from app.services import secrets_store

    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", "key", raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", "secret", raising=False)
    monkeypatch.setattr(epo_ops, "check_credentials", lambda key, secret, **kw: 1199.0)
    out = secrets_store.probe_secret("epo_consumer_key")
    assert out["ok"] is True and "连接成功" in out["message"] and "19 分钟" in out["message"]

    def refused(key, secret, **kw):
        raise epo_ops.EpoOpsError("auth", "EPO OPS 拒绝了这组 Consumer Key / Secret（ClientId is Invalid）", status=401)

    monkeypatch.setattr(epo_ops, "check_credentials", refused)
    out = secrets_store.probe_secret("epo_consumer_secret")
    assert out["ok"] is False and "ClientId is Invalid" in out["message"]

    def unreachable(key, secret, **kw):
        raise httpx.ConnectError("proxy says no")

    monkeypatch.setattr(epo_ops, "check_credentials", unreachable)
    out = secrets_store.probe_secret("epo_consumer_key")
    assert out["ok"] is False and "ConnectError" in out["message"]


# ── patent-client does not come back ─────────────────────────────────────────


def test_nothing_in_the_app_imports_patent_client():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n.split(".")[0] == "patent_client" for n in names):
                offenders.append(f"{path.relative_to(BACKEND)}:{node.lineno}")
    assert not offenders, offenders


def test_the_sdk_is_declared_nowhere_and_its_env_helper_and_probe_functions_are_gone():
    meta = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = [r for reqs in meta["optional-dependencies"].values() for r in reqs] + list(meta["dependencies"])
    assert not [r for r in declared if r.lower().replace("_", "-").startswith("patent-client")]
    assert "patents" not in meta["optional-dependencies"]
    assert not (APP / "services" / "patent_client_env.py").exists()
    assert not hasattr(literature, "_online_search"), "the USPTO path that needed the SDK is gone with it"
