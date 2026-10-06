"""EPO OPS over httpx (``services.epo_ops``): requests, token handling, error mapping, parsing, query building.

Everything here is offline - an ``httpx.MockTransport`` plays OPS - so what it pins is the *documented* contract and the
error bodies recorded from the live service (2026-10-05):

* ``POST /3.2/auth/accesstoken`` with bad credentials -> ``401`` ``<error><code>401</code><message>ClientId is Invalid</message></error>``;
* ``GET .../published-data/search`` with a bad bearer token -> ``400`` ``<error><code>400</code><message>invalid_access_token</message>...``
  (400, not 401), with ``WWW-Authenticate: Bearer ... error="invalid_token"``.

What these tests cannot say is that the authenticated search answers in the shape the fixtures have: no OPS credentials
were available when this was written. ``scripts/audit/epo_ops_smoke.py`` is the check to run with a real key.
"""
from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.services import epo_ops

KEY, SECRET = "consumer-key", "consumer-secret-0123456789"

# ── recorded / documented bodies ─────────────────────────────────────────────

TOKEN_OK = {
    "access_token": "tok-1",
    "expires_in": "1199",  # OPS sends seconds as a string
    "token_type": "BearerToken",
    "status": "approved",
    "scope": "",
    "refresh_count": "0",
}
# recorded from the live service with a made-up client id
TOKEN_REFUSED = "<error><code>401</code><message>ClientId is Invalid</message>\n\t\t\t</error>"
# recorded from the live service with a made-up bearer token
TOKEN_INVALID = (
    "<error><code>400</code><message>invalid_access_token</message>"
    "<description>Access token is invalid</description>\n\t\t\t\t</error>"
)
NOT_FOUND = (
    "<error><code>404</code><message>SERVER.EntityNotFound</message>"
    "<description>No results found</description></error>"
)


def _doc(country, number, kind, date, *, titles, abstract=None, applicants=None, family="123"):
    """One ``exchange-document`` in OPS's XML-to-JSON shape."""
    document = {
        "@system": "ops.epo.org",
        "@family-id": family,
        "@country": country,
        "@doc-number": number,
        "@kind": kind,
        "bibliographic-data": {
            "publication-reference": {
                "document-id": [
                    {
                        "@document-id-type": "docdb",
                        "country": {"$": country},
                        "doc-number": {"$": number},
                        "kind": {"$": kind},
                        "date": {"$": date},
                    },
                    {"@document-id-type": "epodoc", "doc-number": {"$": f"{country}{number}"}, "date": {"$": date}},
                ]
            },
            "invention-title": titles,
        },
    }
    if applicants:
        document["bibliographic-data"]["parties"] = {"applicants": {"applicant": applicants}}
    if abstract is not None:
        document["abstract"] = abstract
    return document


US_DOC = _doc(
    "US", "2020123456", "A1", "20200423",
    titles=[{"@lang": "de", "$": "Korrosionsschutz"}, {"@lang": "en", "$": "Chromium-free passivation of magnesium"}],
    abstract={"@lang": "en", "p": [{"$": "A passivation bath."}, {"$": "It contains no chromium."}]},
    applicants=[
        {"@data-format": "original", "@sequence": "1", "applicant-name": {"name": {"$": "ACME CORP [US]"}}},
        {"@data-format": "epodoc", "@sequence": "1", "applicant-name": {"name": {"$": "ACME CORP"}}},
    ],
    family="7001",
)
EP_DOC = _doc(
    "EP", "3211048", "A1", "20170830",
    titles={"@lang": "en", "$": "Low-temperature curing anticorrosive primer"},  # a single title: a dict, not a list
    abstract={"@lang": "en", "p": {"$": "A primer that cures at 80 C."}},
    applicants={"@data-format": "epodoc", "applicant-name": {"name": {"$": "COATINGS AG"}}},
    family="7002",
)
CN_DOC = _doc("CN", "104789083", "B", "20161207", titles=[{"@lang": "en", "$": "Passivation solution"}], family="7003")


def _payload(*documents, total=None, wrap_each=True):
    """The search reply: each hit in its own ``exchange-documents`` wrapper (a dict when there is only one)."""
    wrapped = [{"exchange-document": d} for d in documents]
    body = wrapped if wrap_each else {"exchange-document": list(documents)}
    result = {"exchange-documents": wrapped[0] if len(wrapped) == 1 and wrap_each else body}
    return {
        "ops:world-patent-data": {
            "@xmlns": {"ops": "http://ops.epo.org", "$": "http://www.epo.org/exchange"},
            "ops:biblio-search": {
                "@total-result-count": str(total if total is not None else len(documents)),
                "ops:query": {"@syntax": "CQL", "$": "ta=magnesium"},
                "ops:range": {"@begin": "1", "@end": str(len(documents))},
                "ops:search-result": result,
            },
        }
    }


# ── a fake OPS ───────────────────────────────────────────────────────────────


class FakeOps:
    """Plays OPS: records every request, answers the token endpoint and the search from queues."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.tokens = [dict(TOKEN_OK)]
        self.token_status = 200
        self.token_body = None
        self.search_replies: list[httpx.Response | Exception] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/auth/accesstoken"):
            if self.token_status != 200:
                return httpx.Response(self.token_status, text=self.token_body or TOKEN_REFUSED, headers={"content-type": "application/xml"})
            token = dict(self.tokens[min(len(self.token_requests()) - 1, len(self.tokens) - 1)])
            return httpx.Response(200, json=token)
        reply = self.search_replies.pop(0) if self.search_replies else httpx.Response(200, json=_payload(US_DOC))
        if isinstance(reply, Exception):
            raise reply
        return reply

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def token_requests(self):
        return [r for r in self.requests if r.url.path.endswith("/auth/accesstoken")]

    def searches(self):
        return [r for r in self.requests if "published-data" in r.url.path]


@pytest.fixture(autouse=True)
def _fresh_tokens():
    epo_ops.forget_tokens()
    yield
    epo_ops.forget_tokens()


@pytest.fixture()
def ops():
    return FakeOps()


def _search(ops, cql="ta=magnesium", **kwargs):
    with ops.client() as client:
        return epo_ops.search(cql, key=KEY, secret=SECRET, client=client, **kwargs)


# ── the requests ─────────────────────────────────────────────────────────────


def test_the_token_request_is_oauth_client_credentials_with_basic_auth(ops):
    _search(ops)
    (token,) = ops.token_requests()
    assert token.method == "POST" and str(token.url) == epo_ops.TOKEN_URL == "https://ops.epo.org/3.2/auth/accesstoken"
    assert token.headers["authorization"] == "Basic " + base64.b64encode(f"{KEY}:{SECRET}".encode()).decode()
    assert token.headers["content-type"] == "application/x-www-form-urlencoded"
    assert token.content == b"grant_type=client_credentials"


def test_the_search_request_carries_the_bearer_token_the_query_and_the_range(ops):
    _search(ops, "ta=magnesium and ta=alloy", begin=26, end=50)
    (search,) = ops.searches()
    assert search.method == "GET"
    assert search.url.path == "/3.2/rest-services/published-data/search/biblio"
    assert parse_qs(urlparse(str(search.url)).query) == {"q": ["ta=magnesium and ta=alloy"]}
    assert search.headers["authorization"] == "Bearer tok-1"
    assert search.headers["accept"] == "application/json"
    assert search.headers["x-ops-range"] == "26-50"
    assert SECRET not in str(search.url) and SECRET not in str(search.headers)


def test_a_token_is_reused_until_it_is_about_to_expire(ops, monkeypatch):
    _search(ops)
    _search(ops)
    assert len(ops.token_requests()) == 1, "the second search must not ask for a new token"

    import time

    real = time.monotonic
    monkeypatch.setattr(epo_ops.time, "monotonic", lambda: real() + 1199 - 10)  # inside the refresh margin
    _search(ops)
    assert len(ops.token_requests()) == 2


def test_tokens_are_per_credential_pair_and_the_secret_is_not_a_cache_key(ops):
    with ops.client() as client:
        epo_ops.search("ta=a", key=KEY, secret=SECRET, client=client)
        epo_ops.search("ta=a", key="other-key", secret=SECRET, client=client)
    assert len(ops.token_requests()) == 2
    assert all(SECRET not in slot and KEY not in slot for slot in epo_ops._tokens)


def test_a_rejected_token_is_refreshed_once_and_the_search_retried(ops):
    """An expired token is a 400 (invalid_access_token), not a 401 - recorded from the live service."""
    ops.tokens = [{**TOKEN_OK, "access_token": "stale"}, {**TOKEN_OK, "access_token": "fresh"}]
    ops.search_replies = [
        httpx.Response(400, text=TOKEN_INVALID, headers={"www-authenticate": 'Bearer realm="null",error="invalid_token"'}),
        httpx.Response(200, json=_payload(US_DOC)),
    ]
    result = _search(ops)
    assert [h.publication_number for h in result.hits] == ["US2020123456A1"]
    assert [r.headers["authorization"] for r in ops.searches()] == ["Bearer stale", "Bearer fresh"]
    assert len(ops.token_requests()) == 2


def test_a_401_on_the_search_is_treated_the_same_way(ops):
    ops.search_replies = [httpx.Response(401, text=TOKEN_REFUSED), httpx.Response(200, json=_payload(US_DOC))]
    assert _search(ops).hits
    assert len(ops.token_requests()) == 2


def test_a_second_refusal_is_a_real_one(ops):
    ops.search_replies = [httpx.Response(400, text=TOKEN_INVALID), httpx.Response(400, text=TOKEN_INVALID)]
    with pytest.raises(epo_ops.EpoOpsError) as caught:
        _search(ops)
    assert caught.value.kind == "auth" and "invalid_access_token" in str(caught.value)
    assert len(ops.token_requests()) == 2  # refreshed once, not in a loop


# ── error mapping ────────────────────────────────────────────────────────────


def test_refused_credentials_say_so_in_the_oauth_error_of_the_live_service(ops):
    ops.token_status = 401
    with pytest.raises(epo_ops.EpoOpsError) as caught:
        _search(ops)
    assert caught.value.kind == "auth" and caught.value.status == 401
    assert "ClientId is Invalid" in str(caught.value)
    assert SECRET not in str(caught.value) and KEY not in str(caught.value)
    assert not ops.searches(), "no search without a token"


def test_no_hits_is_an_empty_result_not_an_error(ops):
    ops.search_replies = [httpx.Response(404, text=NOT_FOUND)]
    result = _search(ops)
    assert result.total == 0 and result.hits == []


def test_a_quota_overrun_is_a_quota_error_naming_the_reason(ops):
    ops.search_replies = [httpx.Response(403, text="<fault/>", headers={"X-Rejection-Reason": "IndividualQuotaPerHour"})]
    with pytest.raises(epo_ops.EpoOpsError) as caught:
        _search(ops)
    assert caught.value.kind == "quota" and "IndividualQuotaPerHour" in str(caught.value)


def test_a_rejected_query_is_reported_with_the_query(ops):
    ops.search_replies = [
        httpx.Response(400, text="<error><code>400</code><message>CLIENT.InvalidQuery</message><description>Unknown index</description></error>")
    ]
    with pytest.raises(epo_ops.EpoOpsError) as caught:
        _search(ops, "zz=magnesium")
    assert caught.value.kind == "bad_query"
    assert "zz=magnesium" in str(caught.value) and "CLIENT.InvalidQuery: Unknown index" in str(caught.value)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_server_error_is_unavailable(ops, status):
    ops.search_replies = [httpx.Response(status, text="boom")]
    with pytest.raises(epo_ops.EpoOpsError) as caught:
        _search(ops)
    assert caught.value.kind == "unavailable" and caught.value.status == status


def test_a_network_failure_is_unavailable_and_does_not_leak_the_url_or_secret(ops):
    ops.search_replies = [httpx.ConnectError("name resolution failed")]
    with pytest.raises(epo_ops.EpoOpsError) as caught:
        _search(ops)
    assert caught.value.kind == "unavailable" and "ConnectError" in str(caught.value)
    assert SECRET not in str(caught.value)


def test_a_reply_that_is_not_json_is_a_parse_error(ops):
    ops.search_replies = [httpx.Response(200, text="<html>maintenance</html>")]
    with pytest.raises(epo_ops.EpoOpsError) as caught:
        _search(ops)
    assert caught.value.kind == "parse"


def test_a_token_reply_without_a_token_is_a_parse_error(ops):
    ops.tokens = [{"status": "approved"}]
    with pytest.raises(epo_ops.EpoOpsError) as caught:
        _search(ops)
    assert caught.value.kind == "parse"


@pytest.mark.parametrize("lifetime, expected", [("1199", 1199.0), (1199, 1199.0), ("soon", 1200.0), ("5", 60.0), ("999999", 3600.0)])
def test_the_token_lifetime_is_read_defensively(ops, lifetime, expected):
    ops.tokens = [{**TOKEN_OK, "expires_in": lifetime}]
    with ops.client() as client:
        assert epo_ops.check_credentials(KEY, SECRET, client=client) == expected


def test_check_credentials_raises_for_a_refused_pair(ops):
    ops.token_status = 401
    with ops.client() as client, pytest.raises(epo_ops.EpoOpsError) as caught:
        epo_ops.check_credentials(KEY, SECRET, client=client)
    assert caught.value.kind == "auth"


def test_a_search_window_larger_than_ops_allows_is_refused_up_front(ops):
    with pytest.raises(ValueError, match="at most 25"):
        _search(ops, begin=1, end=26)
    assert not ops.requests


# ── parsing ──────────────────────────────────────────────────────────────────


def test_a_page_of_hits_is_parsed_from_the_documented_shape():
    result = epo_ops.parse_search(_payload(US_DOC, EP_DOC, CN_DOC, total=57))
    assert result.total == 57
    us, ep, cn = result.hits
    assert us.publication_number == "US2020123456A1" and (us.country, us.number, us.kind) == ("US", "2020123456", "A1")
    assert us.title == "Chromium-free passivation of magnesium", "the English title, not the first one"
    assert us.abstract == "A passivation bath. It contains no chromium."
    assert us.applicant == "ACME CORP", "the epodoc rendering, without the original's '[US]' suffix"
    assert (us.date, us.pub_date, us.pub_year, us.family_id) == ("20200423", "2020-04-23", 2020, "7001")
    assert ep.publication_number == "EP3211048A1" and ep.title == "Low-temperature curing anticorrosive primer"
    assert ep.abstract == "A primer that cures at 80 C." and ep.applicant == "COATINGS AG"
    assert cn.publication_number == "CN104789083B" and cn.abstract == "" and cn.applicant == ""


def test_a_single_hit_is_a_dict_where_several_are_a_list_and_both_parse():
    one = epo_ops.parse_search(_payload(US_DOC))
    assert [h.publication_number for h in one.hits] == ["US2020123456A1"]
    many_in_one_wrapper = epo_ops.parse_search(_payload(US_DOC, EP_DOC, wrap_each=False))
    assert [h.publication_number for h in many_in_one_wrapper.hits] == ["US2020123456A1", "EP3211048A1"]


def test_a_hit_without_a_docdb_id_falls_back_to_the_document_attributes():
    doc = {"@country": "WO", "@doc-number": "2020123456", "@kind": "A1", "bibliographic-data": {}}
    (hit,) = epo_ops.parse_search(_payload(doc)).hits
    assert hit.publication_number == "WO2020123456A1" and hit.title == "" and hit.date == ""
    assert hit.pub_date is None and hit.pub_year is None


def test_a_document_that_cannot_be_identified_is_skipped_not_fatal():
    broken = {"bibliographic-data": {"invention-title": [{"@lang": "en", "$": "no number"}]}}
    result = epo_ops.parse_search(_payload(broken, US_DOC))
    assert [h.publication_number for h in result.hits] == ["US2020123456A1"]


def test_the_same_publication_twice_is_one_hit():
    assert len(epo_ops.parse_search(_payload(US_DOC, US_DOC)).hits) == 1


def test_a_total_that_is_missing_or_wrong_never_undercounts_the_hits():
    payload = _payload(US_DOC, EP_DOC)
    payload["ops:world-patent-data"]["ops:biblio-search"]["@total-result-count"] = "oops"
    assert epo_ops.parse_search(payload).total == 2


@pytest.mark.parametrize("payload", [{}, {"ops:world-patent-data": {}}, {"something": [1, 2, 3]}])
def test_an_unrecognised_object_has_no_hits(payload):
    assert epo_ops.parse_search(payload).hits == []


@pytest.mark.parametrize("payload", [[], "text", None, 7])
def test_something_that_is_not_an_object_is_a_parse_error(payload):
    with pytest.raises(epo_ops.EpoOpsError) as caught:
        epo_ops.parse_search(payload)
    assert caught.value.kind == "parse"


# ── queries ──────────────────────────────────────────────────────────────────


def test_a_query_becomes_a_conjunction_of_its_distinctive_words():
    cql = epo_ops.build_cql("Chromium-free passivation of magnesium alloy, corrosion resistance")
    assert cql == "ta=chromium and ta=free and ta=passivation and ta=magnesium and ta=alloy and ta=corrosion"


def test_the_number_of_terms_is_capped_and_repeats_and_stop_words_are_dropped():
    assert epo_ops.build_cql("the coating and the coating of a method", max_terms=6) == "ta=coating"
    assert epo_ops.build_cql("zinc phosphate epoxy primer coating steel", max_terms=3) == "ta=zinc and ta=phosphate and ta=epoxy"


@pytest.mark.parametrize(
    "hostile",
    ['magnesium" or pa="x', "alloy) or (ti=secret", "zinc/*comment*/ prox/distance<3", "a=b and c=d", "ta=ti=pa", "x\ny\tz", "%22%29"],
)
def test_nothing_in_a_query_can_inject_cql_syntax(hostile):
    cql = epo_ops.build_cql(hostile) or ""
    leftover = cql
    for term in epo_ops.query_terms(hostile):
        leftover = leftover.replace(f"ta={term}", "")
    leftover = leftover.replace(" and ", "")
    assert leftover == "", f"{cql!r} contains more than index=word terms joined by 'and'"
    assert '"' not in cql and "(" not in cql and "/" not in cql


def test_cql_operators_are_never_terms():
    assert epo_ops.build_cql("coating or and not prox steel") == "ta=coating and ta=steel"


def test_cpc_classes_are_quoted_and_joined_with_or_and_limited_to_two():
    assert epo_ops.build_cql("zinc", ["c23c22", "C09D5/08", "B05D"]) == 'ta=zinc and (cpc="C23C22" or cpc="C09D5/08")'
    assert epo_ops.build_cql("", ["C23C22"]) == '(cpc="C23C22")', "a classification alone is a query"
    assert epo_ops.build_cql("zinc", ['C23"; DROP']) == 'ta=zinc and (cpc="C23DROP")', "symbols are cleaned, not trusted"


@pytest.mark.parametrize("query", ["", "   ", "镁合金无铬钝化", "of the and", None])
def test_nothing_searchable_is_no_query(query):
    assert epo_ops.build_cql(query) is None
