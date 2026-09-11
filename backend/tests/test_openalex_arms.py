"""Multi-arm OpenAlex retrieval, the rate-limit retry, and the project quotas.

Three things are pinned here, all of which were measured on the live API before
being written:

* the *shape* of the arm queries — OpenAlex ANDs bare words and stems them, so a
  single concatenated query returned 1 hit for a topic the arms return 51 for;
* the operator budget — >5 boolean operators drops the client into a 5 req/s
  bucket (`reason: broad_boolean_query`), and an un-retried 429 empties a whole
  arm silently;
* the quotas — per-run caps cannot bound a project that accumulates, and the PDF
  path is the memory-bound one.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence, ProductDomain, Requirement, Substrate
from app.services import literature, search_providers as sp


@pytest.fixture(autouse=True)
def _fresh():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _req(substrate=Substrate.magnesium_alloy, domain=ProductDomain.surface_treatment):
    return Requirement(domain=domain, substrate=substrate)


TERMS = [
    "magnesium alloy",
    "chromium-free passivation",
    "passivating agent",
    "conversion coating",
    "surface treatment",
]


def _ops(q: str) -> int:
    return q.count(" OR ") + q.count(" AND ")


# ── arm construction ─────────────────────────────────────────────────────────


def test_arms_are_precise_recall_and_broad():
    from app.domain.search_profiles import get_profile

    arms = sp.arm_queries(TERMS, req=_req(), profile=get_profile("surface_treatment"))
    assert [a.name for a in arms] == ["precise", "recall", "broad"]
    assert [a.conditional for a in arms] == [False, False, True]


def test_precise_arm_caps_term_count():
    """Measured cliff: 2 terms 91 hits, 4 terms 7, 8 terms 1 (degreaser topic)."""
    from app.domain.search_profiles import get_profile

    long_terms = TERMS + ["extra one", "extra two", "extra three", "extra four"]
    arms = sp.arm_queries(
        long_terms, req=_req(), profile=get_profile("surface_treatment"),
    )
    precise = arms[0].query
    # The cap counts *terms*, not words — "magnesium alloy" is one term of two
    # words — so compare term lists rather than len(query.split()).
    assert precise == " ".join(long_terms[:8]), "arm 1 is capped at openalex_arm_precise_max_terms"
    assert "extra four" not in precise
    assert _ops(precise) == 0, "the precise arm is a plain AND string, not a boolean"


def test_arm_operator_budget_stays_in_the_fast_lane():
    """`more than 5 operators` = rate-limited to 5 req/s. Two groups of 3 terms is
    exactly 5 (2 + 2 + 1), so every arm must stay at or under it."""
    from app.domain.search_profiles import get_profile

    arms = sp.arm_queries(
        TERMS * 3, req=_req(), profile=get_profile("surface_treatment")
    )
    for arm in arms:
        assert _ops(arm.query) <= 5, f"{arm.name} uses {_ops(arm.query)} operators"


def test_arms_are_substrate_anchored():
    from app.domain.search_profiles import get_profile

    arms = sp.arm_queries(TERMS, req=_req(), profile=get_profile("surface_treatment"))
    for arm in arms[1:]:
        assert "magnesium" in arm.query, "recall/broad must hold the substrate"


def test_no_recall_arms_without_a_substrate():
    """A bare OR measurably loses precision (domain hit 100/28/29% vs 100%),
    and `Requirement.substrate` is non-nullable with a default, so this only
    happens for an ad-hoc call with no requirement at all."""
    from app.domain.search_profiles import get_profile

    arms = sp.arm_queries(TERMS, req=None, profile=get_profile("surface_treatment"))
    assert [a.name for a in arms] == ["precise"]


def test_arm_weights_order_precise_above_broad():
    from app.domain.search_profiles import get_profile

    arms = {a.name: a for a in sp.arm_queries(
        TERMS, req=_req(), profile=get_profile("surface_treatment"))}
    assert arms["precise"].relevance_delta > arms["recall"].relevance_delta > arms["broad"].relevance_delta


# ── merge + rescue ───────────────────────────────────────────────────────────


def _ev(ident: str, *, arm: str, relevance: float = 0.8) -> Evidence:
    return Evidence(
        source="OpenAlex", identifier=ident, title=f"t {ident}", snippet="s",
        relevance=relevance, domain_tags=[f"arm:{arm}"],
    )


def _stub_arms(monkeypatch, per_arm: dict[str, list[Evidence]]):
    """Stub `search_openalex` to answer by arm name and record the queries."""
    seen: list[tuple[str, str]] = []

    def fake(query, limit=5, offset=0, **kw):
        arm = kw.get("arm") or "single"
        seen.append((arm, query))
        return list(per_arm.get(arm, []))

    monkeypatch.setattr(literature, "search_openalex", fake)
    return seen


def test_arms_merge_and_dedupe_by_doi(monkeypatch):
    _stub_arms(monkeypatch, {
        "precise": [_ev("10.1/a", arm="precise"), _ev("doi:10.1/b", arm="precise")],
        "recall": [_ev("https://doi.org/10.1/A", arm="recall"), _ev("10.1/c", arm="recall")],
    })
    out = literature.openalex_arms(TERMS, 25, offset=0, domain="surface_treatment", req=_req())
    # 10.1/a vs https://doi.org/10.1/A is the same work under three different
    # spellings; without normalisation the union double-counts it.
    assert [e.identifier for e in out] == ["10.1/a", "doi:10.1/b", "10.1/c"]


def test_precise_rows_win_the_dedupe_tie(monkeypatch):
    _stub_arms(monkeypatch, {
        "precise": [_ev("10.1/a", arm="precise", relevance=0.9)],
        "recall": [_ev("10.1/a", arm="recall", relevance=0.5)],
    })
    out = literature.openalex_arms(TERMS, 25, offset=0, domain="surface_treatment", req=_req())
    assert len(out) == 1
    assert "arm:precise" in (out[0].domain_tags or [])


def test_broad_arm_fires_only_when_the_others_come_back_thin(monkeypatch):
    """It returns hundreds of thousands of hits and drops the user's wording, so
    it is a rescue net — not a paging source."""
    seen = _stub_arms(monkeypatch, {
        "precise": [_ev(f"10.1/p{i}", arm="precise") for i in range(4)],
        "recall": [],
        "broad": [_ev("10.1/broad1", arm="broad")],
    })
    out = literature.openalex_arms(TERMS, 25, offset=0, domain="surface_treatment", req=_req())
    assert [a for a, _ in seen] == ["precise", "recall", "broad"]
    assert any("arm:broad" in (e.domain_tags or []) for e in out)


def test_broad_arm_stays_quiet_when_there_is_enough(monkeypatch):
    seen = _stub_arms(monkeypatch, {
        "precise": [_ev(f"10.1/p{i}", arm="precise") for i in range(25)],
        "recall": [_ev(f"10.1/r{i}", arm="recall") for i in range(25)],
        "broad": [_ev("10.1/broad1", arm="broad")],
    })
    literature.openalex_arms(TERMS, 25, offset=0, domain="surface_treatment", req=_req())
    assert "broad" not in [a for a, _ in seen]


def test_broad_arm_is_not_a_paging_source(monkeypatch):
    """It rescues page 1 only; paging would multiply the expensive query."""
    seen = _stub_arms(monkeypatch, {
        "precise": [], "recall": [], "broad": [_ev("10.1/b", arm="broad")],
    })
    literature.openalex_arms(TERMS, 25, offset=25, domain="surface_treatment", req=_req())
    assert "broad" not in [a for a, _ in seen]


def test_switching_multi_arm_off_restores_the_single_query(monkeypatch):
    monkeypatch.setattr(get_settings(), "openalex_multi_arm", False, raising=False)
    seen = _stub_arms(monkeypatch, {"single": [_ev("10.1/a", arm="single")]})
    out = literature.openalex_arms(TERMS, 25, offset=0, domain="surface_treatment", req=_req())
    assert len(seen) == 1
    assert seen[0][0] == "single"
    assert seen[0][1] == " ".join(TERMS)
    assert len(out) == 1


# ── rate-limit retry ─────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None):
        self.calls += 1
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


def test_429_is_retried_honouring_retry_after(monkeypatch):
    """Without this an arm empties silently: measured mid-session, the precise
    arm returned 25 rows while recall and broad both returned 0."""
    slept: list[float] = []
    monkeypatch.setattr(sp.time, "sleep", lambda s: slept.append(s))
    client = _Client([_Resp(429, {"retryAfter": 1}), _Resp(200)])
    resp = sp._get_with_retry(client, "u", {})
    assert resp.status_code == 200
    assert client.calls == 2
    assert slept == [1.0]


def test_retry_gives_up_after_the_budget(monkeypatch):
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    client = _Client([_Resp(429, {"retryAfter": 0.01})])
    resp = sp._get_with_retry(client, "u", {})
    assert resp.status_code == 429
    assert client.calls == sp._RATE_LIMIT_RETRIES + 1


def test_healthy_response_is_not_delayed(monkeypatch):
    monkeypatch.setattr(sp.time, "sleep", lambda s: pytest.fail("must not sleep"))
    client = _Client([_Resp(200)])
    assert sp._get_with_retry(client, "u", {}).status_code == 200
    assert client.calls == 1


def test_response_without_status_code_is_passed_through():
    """Test doubles often implement only raise_for_status/json; reading
    status_code off them would raise into the caller's blanket except and turn a
    healthy search into zero hits."""
    class _Bare:
        def json(self):
            return {}

    assert sp._get_with_retry(_Client([_Bare()]), "u", {}) is not None


# ── relevance shadow (M2) ────────────────────────────────────────────────────


def test_topicality_is_keyword_overlap_not_rank():
    from app.services.kb_ingest import _topicality

    on_topic = Evidence(source="OpenAlex", identifier="1", title="Magnesium alloy passivation",
                        snippet="chrome-free conversion coating", relevance=0.52)
    off_topic = Evidence(source="OpenAlex", identifier="2", title="Quantum computing",
                         snippet="qubit entanglement", relevance=0.99)
    q = "magnesium passivation chrome-free"
    assert _topicality(on_topic, q) > _topicality(off_topic, q)
    # relevance is a rank-position proxy, so it ranks the off-topic row HIGHER —
    # which is exactly why kb_ingest_min_relevance rejects nothing on page one.
    assert off_topic.relevance > on_topic.relevance


def test_shadow_mode_rejects_nothing(monkeypatch):
    """The whole point of shadow mode: log what a real gate would drop, change
    no behaviour."""
    from app.services import kb_ingest

    logged: list = []
    monkeypatch.setattr(kb_ingest.logger, "info", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(get_settings(), "kb_relevance_shadow", True, raising=False)
    monkeypatch.setattr(get_settings(), "kb_project_source_quota", 0, raising=False)

    rows = [
        Evidence(source="OpenAlex", identifier=f"10.1000/shad{i}", title="Magnesium alloy passivation",
                 snippet="chrome-free conversion coating", relevance=0.99)
        for i in range(5)
    ]
    targets = kb_ingest.select_ingest_targets(
        rows, project_id=None, query="magnesium passivation",
        skip_topic_filter=True, write_audit=False,
    )
    assert len(targets) == 5, "shadow mode must not drop rows"
    assert any("relevance shadow" in str(a[0]) for a in logged)


# ── per-project quotas (M3) ──────────────────────────────────────────────────


def test_source_quota_blocks_when_the_project_is_full(monkeypatch):
    from app.services import kb_ingest

    monkeypatch.setattr(get_settings(), "kb_project_source_quota", 300, raising=False)
    monkeypatch.setattr(
        "app.db.source_store.SourceStore.count_for_project", lambda self, pid, **kw: 300
    )
    rows = [Evidence(source="OpenAlex", identifier="10.1000/q1", title="t", snippet="s", relevance=0.9)]
    targets = kb_ingest.select_ingest_targets(
        rows, project_id="p1", query="q", skip_topic_filter=True, write_audit=False
    )
    assert targets == []


def test_source_quota_leaves_exactly_the_remaining_room(monkeypatch):
    from app.services import kb_ingest

    monkeypatch.setattr(get_settings(), "kb_project_source_quota", 300, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_max_docs", 0, raising=False)
    monkeypatch.setattr(
        "app.db.source_store.SourceStore.count_for_project", lambda self, pid, **kw: 298
    )
    rows = [
        Evidence(source="OpenAlex", identifier=f"10.1000/q{i}", title="zinc phosphate epoxy primer",
                 snippet="corrosion protection", relevance=0.9)
        for i in range(10)
    ]
    targets = kb_ingest.select_ingest_targets(
        rows, project_id="p1", query="zinc phosphate epoxy primer",
        skip_topic_filter=True, write_audit=False,
    )
    assert len(targets) == 2


def test_quota_is_ignored_without_a_project(monkeypatch):
    """Ad-hoc ingest has no project to scope and keeps the legacy behaviour."""
    from app.services import kb_ingest

    monkeypatch.setattr(get_settings(), "kb_project_source_quota", 1, raising=False)
    rows = [
        Evidence(source="OpenAlex", identifier=f"10.1000/z{i}", title="zinc phosphate",
                 snippet="primer", relevance=0.9)
        for i in range(4)
    ]
    targets = kb_ingest.select_ingest_targets(
        rows, project_id=None, query="zinc phosphate",
        skip_topic_filter=True, write_audit=False,
    )
    assert len(targets) == 4
