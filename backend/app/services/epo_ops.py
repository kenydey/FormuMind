"""EPO Open Patent Services (OPS 3.2) over plain httpx - the official REST interface, no SDK.

This replaces the ``patent-client`` package. Every release of that SDK requires ``httpx<0.28`` and ``pypdf<5``, so
installing it made pip *replace* the backend's pinned httpx and pypdf (49 advisories on the pypdf it forced), it
supports only Python < 3.13, and it was the only way the app searched the EPO and the USPTO. OPS needs nothing but an
HTTP client and a free consumer key / secret (https://developers.epo.org); its DOCDB database covers the US, EP, WO,
CN, JP, KR, ... publications, so one provider answers for both offices.

What is used, and where it is documented (OPS 3.2 reference guide):

* ``POST /3.2/auth/accesstoken`` - OAuth2 client credentials (HTTP Basic ``key:secret``,
  ``grant_type=client_credentials``). The reply is JSON with ``access_token`` and ``expires_in`` (a string of seconds,
  about 20 minutes). Bad credentials: ``401`` with ``<error><code>401</code><message>ClientId is Invalid</message></error>``.
* ``GET /3.2/rest-services/published-data/search/biblio?q=<CQL>`` with ``Authorization: Bearer`` and the result window
  in the ``X-OPS-Range`` header (``1-25``; at most 25 bibliographic records per request). JSON is the XML tree with
  attributes as ``@name`` and text as ``$``, and *a single child is a dict where several are a list* - hence the
  tolerant walker in :func:`parse_search`. No hits is ``404``, not an empty ``200``. An invalid or expired token is
  ``400`` (``invalid_access_token``), not ``401``. A quota overrun is ``403`` with ``X-Rejection-Reason``.

Verified against the live service: the token endpoint and both error shapes above (no credentials were available, so
the authenticated search was **not** exercised - the parser is written from the documented structure and covered by
recorded-shape tests; ``scripts/audit/epo_ops_smoke.py`` runs the whole chain with your credentials and prints what
came back).
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from .http_safe import make_client

logger = logging.getLogger(__name__)

BASE_URL = "https://ops.epo.org/3.2"
TOKEN_URL = f"{BASE_URL}/auth/accesstoken"
SEARCH_URL = f"{BASE_URL}/rest-services/published-data/search/biblio"

# OPS returns at most this many bibliographic records for one search request.
PAGE_SIZE = 25
# Refresh a token this long before OPS says it expires (clock skew, the request in flight).
_TOKEN_MARGIN_S = 30.0
_DEFAULT_TOKEN_TTL_S = 1200.0


class EpoOpsError(RuntimeError):
    """An OPS call failed. ``kind`` says why, so callers can tell a configuration problem from an outage:

    ``auth`` (credentials refused), ``quota`` (fair-use limit reached), ``bad_query`` (CQL rejected), ``unavailable``
    (5xx / network), ``parse`` (a 200 that was not the documented shape).
    """

    def __init__(self, kind: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status


@dataclass(frozen=True)
class OpsHit:
    country: str
    number: str
    kind: str = ""
    date: str = ""  # YYYYMMDD as OPS gives it
    title: str = ""
    abstract: str = ""
    applicant: str = ""
    family_id: str = ""

    @property
    def publication_number(self) -> str:
        """``US2020123456A1`` / ``EP3211048A1`` / ``CN104789083B`` - the compact form the rest of the app uses."""
        return f"{self.country}{self.number}{self.kind}"

    @property
    def pub_date(self) -> str | None:
        return f"{self.date[:4]}-{self.date[4:6]}-{self.date[6:8]}" if re.fullmatch(r"\d{8}", self.date) else None

    @property
    def pub_year(self) -> int | None:
        return int(self.date[:4]) if re.fullmatch(r"\d{8}", self.date) else None


@dataclass
class OpsResult:
    total: int = 0
    hits: list[OpsHit] = field(default_factory=list)


# ── queries ──────────────────────────────────────────────────────────────────

_TERM = re.compile(r"[A-Za-z0-9]+")
# CQL operators must never become search terms, and the rest only dilute an AND of title/abstract words.
_STOP = frozenset(
    "a an and are as at be by for from has have in into is it its of on or not prox that the their this to using use "
    "used with without via based method methods process processes composition compositions system systems device "
    "apparatus comprising comprises thereof preparation".split()
)


def query_terms(query: str, *, limit: int = 6) -> list[str]:
    """The distinctive ASCII words of a free-text query, in the order given, without repeats."""
    seen: list[str] = []
    for word in _TERM.findall(query or ""):
        word = word.lower()
        if len(word) < 3 or word in _STOP or word in seen:
            continue
        seen.append(word)
        if len(seen) >= limit:
            break
    return seen


def build_cql(query: str, cpc_codes: Iterable[str] | None = None, *, max_terms: int = 6) -> str | None:
    """CQL for OPS from free text and optional CPC classes: ``ta=word and ta=word and (cpc="C23C22" or ...)``.

    Only ``index=term``, ``and``/``or`` and quoted classification symbols are used - the forms the OPS guide gives as its
    examples - and every term is a bare alphanumeric word, so nothing in a user's query can inject CQL syntax. ``ta``
    is "title or abstract". Returns None when there is nothing to search for (an empty or non-ASCII query and no
    classification): Chinese queries are the CNIPA / Google Patents providers' business.
    """
    clauses = [f"ta={term}" for term in query_terms(query, limit=max_terms)]
    codes = []
    for raw in list(cpc_codes or [])[:2]:
        code = re.sub(r"[^A-Za-z0-9/]", "", str(raw)).upper()
        if code and code not in codes:
            codes.append(code)
    if codes:
        clauses.append("(" + " or ".join(f'cpc="{code}"' for code in codes) + ")")
    return " and ".join(clauses) or None


# ── tokens ───────────────────────────────────────────────────────────────────

_tokens: dict[str, tuple[str, float]] = {}
_tokens_lock = threading.Lock()


def _token_slot(key: str, secret: str) -> str:
    # The secret never sits in a dict key (or a traceback that prints one).
    return hashlib.sha256(f"{key}:{secret}".encode()).hexdigest()[:24]


def forget_tokens() -> None:
    """Drop every cached token (tests; also what a credentials change should do)."""
    with _tokens_lock:
        _tokens.clear()


_ERROR_MESSAGE = re.compile(r"<message>\s*(.*?)\s*</message>", re.S)
_ERROR_DESCRIPTION = re.compile(r"<description>\s*(.*?)\s*</description>", re.S)


def _fault(resp: httpx.Response) -> str:
    """The human-readable part of an OPS error body (``<error><code/><message/><description/></error>``)."""
    body = resp.text or ""
    parts = [m.group(1) for rx in (_ERROR_MESSAGE, _ERROR_DESCRIPTION) if (m := rx.search(body))]
    return ": ".join(parts) if parts else body.strip()[:160] or f"HTTP {resp.status_code}"


def fetch_token(client: httpx.Client, key: str, secret: str) -> tuple[str, float]:
    """A fresh access token and its lifetime in seconds."""
    try:
        resp = client.post(
            TOKEN_URL,
            auth=(key, secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise EpoOpsError("unavailable", f"EPO OPS 不可达：{type(exc).__name__}") from exc
    if resp.status_code in (400, 401, 403):
        raise EpoOpsError("auth", f"EPO OPS 拒绝了这组 Consumer Key / Secret（{_fault(resp)}）", status=resp.status_code)
    if resp.status_code >= 400:
        raise EpoOpsError("unavailable", f"EPO OPS 令牌接口返回 HTTP {resp.status_code}", status=resp.status_code)
    try:
        payload = resp.json()
        token = str(payload["access_token"])
    except (ValueError, KeyError, TypeError) as exc:
        raise EpoOpsError("parse", "EPO OPS 令牌接口返回的内容里没有 access_token", status=resp.status_code) from exc
    try:
        ttl = float(payload.get("expires_in", _DEFAULT_TOKEN_TTL_S))
    except (TypeError, ValueError):
        ttl = _DEFAULT_TOKEN_TTL_S
    return token, max(60.0, min(ttl, 3600.0))


def access_token(client: httpx.Client, key: str, secret: str, *, force: bool = False) -> str:
    slot = _token_slot(key, secret)
    now = time.monotonic()
    with _tokens_lock:
        cached = _tokens.get(slot)
        if cached and not force and cached[1] - _TOKEN_MARGIN_S > now:
            return cached[0]
    token, ttl = fetch_token(client, key, secret)
    with _tokens_lock:
        _tokens[slot] = (token, time.monotonic() + ttl)
    return token


def check_credentials(key: str, secret: str, *, timeout: float = 15.0, client: httpx.Client | None = None) -> float:
    """Ask OPS for a token - the cheapest call that proves the key / secret pair is accepted. Returns the token's
    lifetime in seconds; raises :class:`EpoOpsError` (``auth`` when refused)."""
    if client is not None:
        return fetch_token(client, key, secret)[1]
    with make_client(timeout=timeout) as owned:
        return fetch_token(owned, key, secret)[1]


# ── search ───────────────────────────────────────────────────────────────────


def _get(client: httpx.Client, token: str, cql: str, begin: int, end: int) -> httpx.Response:
    try:
        return client.get(
            SEARCH_URL,
            params={"q": cql},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "X-OPS-Range": f"{begin}-{end}",
            },
        )
    except httpx.HTTPError as exc:
        raise EpoOpsError("unavailable", f"EPO OPS 不可达：{type(exc).__name__}") from exc


def _token_rejected(resp: httpx.Response) -> bool:
    # An expired / unknown token is a 400 whose body (and WWW-Authenticate header) say invalid_access_token.
    if resp.status_code == 401:
        return True
    return resp.status_code == 400 and (
        "invalid_access_token" in (resp.text or "") or "invalid_token" in resp.headers.get("www-authenticate", "")
    )


def search(
    cql: str,
    *,
    key: str,
    secret: str,
    begin: int = 1,
    end: int = PAGE_SIZE,
    timeout: float = 15.0,
    client: httpx.Client | None = None,
) -> OpsResult:
    """One page of bibliographic search results for a CQL query. No hits is an empty result, not an error."""
    if end < begin or end - begin + 1 > PAGE_SIZE:
        raise ValueError(f"OPS returns at most {PAGE_SIZE} bibliographic records per request, asked for {begin}-{end}")
    if client is not None:
        return _search(client, cql, key, secret, begin, end)
    with make_client(timeout=timeout) as owned:
        return _search(owned, cql, key, secret, begin, end)


def _search(client: httpx.Client, cql: str, key: str, secret: str, begin: int, end: int) -> OpsResult:
    resp = _get(client, access_token(client, key, secret), cql, begin, end)
    if _token_rejected(resp):
        # Expired between our clock and theirs: one fresh token, one retry - a second refusal is a real one.
        resp = _get(client, access_token(client, key, secret, force=True), cql, begin, end)
        if _token_rejected(resp):
            raise EpoOpsError("auth", f"EPO OPS 不接受令牌（{_fault(resp)}）", status=resp.status_code)
    if resp.status_code == 404:
        return OpsResult()
    if resp.status_code in (403, 429):
        reason = resp.headers.get("x-rejection-reason") or _fault(resp)
        raise EpoOpsError("quota", f"EPO OPS 拒绝了请求（配额或限流：{reason}）", status=resp.status_code)
    if resp.status_code == 400:
        raise EpoOpsError("bad_query", f"EPO OPS 不接受这条查询 {cql!r}（{_fault(resp)}）", status=400)
    if resp.status_code >= 400:
        raise EpoOpsError("unavailable", f"EPO OPS 返回 HTTP {resp.status_code}（{_fault(resp)}）", status=resp.status_code)
    try:
        payload = resp.json()
    except ValueError as exc:
        raise EpoOpsError("parse", "EPO OPS 检索返回的不是 JSON", status=resp.status_code) from exc
    return parse_search(payload)


# ── parsing ──────────────────────────────────────────────────────────────────


def _listify(value: Any) -> list[Any]:
    """OPS's XML→JSON turns a single child into a dict and several into a list; callers want a list either way."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(node: Any) -> str:
    """The text of a ``{"$": "..."}`` node, a bare string, or the first text found in a list of them."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, list):
        return next((t for t in (_text(item) for item in node) if t), "")
    if isinstance(node, dict):
        return _text(node.get("$"))
    return ""


def _in_language(nodes: Any, *, prefer: str = "en") -> Any:
    """The node written in ``prefer`` (OPS tags titles and abstracts with ``@lang``), else the first one."""
    items = [n for n in _listify(nodes) if n is not None]
    for item in items:
        if isinstance(item, dict) and str(item.get("@lang", "")).lower() == prefer:
            return item
    return items[0] if items else None


def _walk(node: Any, key: str) -> Iterable[Any]:
    """Every value stored under ``key`` anywhere below ``node`` - the result wrapper's nesting varies between OPS
    responses (each hit is wrapped in its own ``exchange-documents``), the documents themselves do not."""
    if isinstance(node, dict):
        for name, child in node.items():
            if name == key:
                yield from _listify(child)
            else:
                yield from _walk(child, key)
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child, key)


_BRACKETED_COUNTRY = re.compile(r"\s*\[[A-Z]{2}\]\s*$")


def _applicant(bibliographic: dict) -> str:
    applicants = _listify(((bibliographic.get("parties") or {}).get("applicants") or {}).get("applicant"))
    # Prefer the epodoc rendering (plain upper-case name); the 'original' one carries a "[US]" suffix.
    ordered = sorted(
        (a for a in applicants if isinstance(a, dict)),
        key=lambda a: 0 if a.get("@data-format") == "epodoc" else 1,
    )
    for entry in ordered:
        name = _text((entry.get("applicant-name") or {}).get("name"))
        if name:
            return _BRACKETED_COUNTRY.sub("", name)
    return ""


def _hit(document: dict) -> OpsHit | None:
    bibliographic = document.get("bibliographic-data") or {}
    docdb = None
    for ref in _listify(bibliographic.get("publication-reference")):
        for doc_id in _listify((ref or {}).get("document-id")):
            if isinstance(doc_id, dict) and doc_id.get("@document-id-type") == "docdb":
                docdb = doc_id
                break
        if docdb:
            break
    docdb = docdb or {}
    country = _text(docdb.get("country")) or str(document.get("@country", ""))
    number = _text(docdb.get("doc-number")) or str(document.get("@doc-number", ""))
    if not (country and number):
        return None
    abstract = _in_language(document.get("abstract"))
    abstract_text = ""
    if isinstance(abstract, dict):
        abstract_text = " ".join(t for t in (_text(p) for p in _listify(abstract.get("p"))) if t)
    return OpsHit(
        country=country.upper(),
        number=number,
        kind=_text(docdb.get("kind")) or str(document.get("@kind", "")),
        date=_text(docdb.get("date")),
        title=_text(_in_language(bibliographic.get("invention-title"))),
        abstract=abstract_text,
        applicant=_applicant(bibliographic),
        family_id=str(document.get("@family-id", "")),
    )


def parse_search(payload: Any) -> OpsResult:
    """The hits and the total of an OPS ``published-data/search/biblio`` JSON reply. Unknown shapes give an empty
    result rather than an exception half-way through a page: whatever parsed is kept."""
    if not isinstance(payload, dict):
        raise EpoOpsError("parse", "EPO OPS 检索返回的 JSON 不是对象")
    hits: list[OpsHit] = []
    seen: set[str] = set()
    for document in _walk(payload, "exchange-document"):
        if not isinstance(document, dict):
            continue
        hit = _hit(document)
        if hit is not None and hit.publication_number not in seen:
            seen.add(hit.publication_number)
            hits.append(hit)
    total = 0
    for search_node in _walk(payload, "ops:biblio-search"):
        try:
            total = int(str((search_node or {}).get("@total-result-count", "0")))
        except (TypeError, ValueError):
            total = 0
        break
    return OpsResult(total=max(total, len(hits)), hits=hits)
