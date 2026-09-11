"""External search providers — OpenAlex, SerpAPI, Tavily, Google Patents CN, CNIPA parallel.

Each function returns normalized :class:`~app.domain.schemas.Evidence` lists and
swallows network errors (logs + returns []).
"""
from __future__ import annotations

from .errors import degrade_return
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from ..config import Settings, get_settings
from ..domain.schemas import Evidence
from ..services.runtime_secrets import effective_setting

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 25.0
# OpenAlex broad-boolean 429s (queries with >5 OR/AND/NOT) carry retryAfter.
_RATE_LIMIT_RETRIES = 2
_PATENT_ID_RE = re.compile(r"[\s\-/]")


def _normalize_patent_id(identifier: str) -> str:
    return _PATENT_ID_RE.sub("", (identifier or "").upper())


def merge_patent_evidence(*lists: list[Evidence], limit: int) -> list[Evidence]:
    """Dedupe patent hits by normalized publication number, preserve first-seen order."""
    seen: set[str] = set()
    out: list[Evidence] = []
    for batch in lists:
        for e in batch:
            key = _normalize_patent_id(e.identifier) if e.identifier else (e.title or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(e)
            if len(out) >= limit:
                return out
    return out


def _ranked(i: int, offset: int = 0) -> float:
    return round(max(0.1, 1.0 - (offset + i) * 0.02), 3)


def _get_with_retry(client, url: str, params: dict):
    """GET, retrying the documented broad-boolean rate limit.

    OpenAlex answers queries with more than five boolean operators with a 429
    carrying ``reason: broad_boolean_query`` and ``retryAfter`` (that bucket is
    capped at 5 req/s). The arm queries sit in that bucket, and an un-retried 429
    empties an entire arm *silently* — measured mid-session: the precise arm
    returned 25 rows while recall and broad both returned 0, leaving a merged
    stream that looked perfectly healthy. Honouring ``retryAfter`` is cheap; the
    API explicitly asks for it.
    """
    delay = 1.0
    resp = client.get(url, params=params)
    for _ in range(_RATE_LIMIT_RETRIES):
        # `getattr`: several test doubles implement only `raise_for_status` /
        # `json`, and reading `status_code` off them would raise straight into
        # the caller's blanket except — turning a healthy search into zero hits.
        if getattr(resp, "status_code", None) != 429:
            return resp
        try:
            delay = float((resp.json() or {}).get("retryAfter") or 1.0)
        except Exception:
            delay = 1.0
        time.sleep(max(0.2, min(delay, 5.0)))
        resp = client.get(url, params=params)
    return resp


@dataclass(frozen=True)
class ArmQuery:
    """One retrieval arm: an OpenAlex query string plus its relevance nudge."""

    name: str
    query: str
    relevance_delta: float = 0.0
    conditional: bool = False


def arm_queries(
    terms: Sequence[str],
    *,
    req=None,
    profile=None,
    settings: Settings | None = None,
    max_precise_terms: int | None = None,
) -> list[ArmQuery]:
    """Build the precise / recall / broad query set for one OpenAlex source.

    The single concatenated query is a lottery: OpenAlex ANDs space-separated
    words and stems them, so a 20-word expansion collapses inside a narrower
    topic — measured, 「铝合金碱性脱脂剂」 returned **1** hit with a 0% domain
    hit rate, against 37 for the merged arms. The three arms trade differently:

    * ``precise`` — the expansion truncated to ``max_precise_terms``. Keeps the
      user's specific intent; highest quality per row.
    * ``recall`` — ``(substrate…) AND (terms OR…)``. Fixes the stemming drift
      that makes a bare OR pull in "passive tracers" for "passivation", while
      opening the AND collar. Biggest single recall win (89 → 2,722 measured).
    * ``broad`` — ``(substrate…) AND (domain vocabulary OR…)``. A rescue arm:
      measured 279k-599k hits and the best domain hit rate, but it drops the
      user's own wording, so it only fires when the other two came back thin.
    """
    settings = settings or get_settings()
    cap = max_precise_terms if max_precise_terms is not None else settings.openalex_arm_precise_max_terms
    terms = [str(t).strip() for t in (terms or ()) if str(t).strip()]
    if not terms:
        return []

    def _or_group(items: Sequence[str]) -> str:
        return "(" + " OR ".join(items) + ")"

    # OpenAlex rate-limits queries with **more than 5 boolean operators** to 5
    # requests/second (`reason: broad_boolean_query`), so keep the total budget
    # at or under that: two groups of ≤3 terms is 2+2+1 = 5 operators. Measured
    # cost of the smaller groups: 78,763 vs 128,494 corpus hits for the same
    # topic — irrelevant when only the top page is taken, and both dwarf the
    # 89-hit baseline this exists to fix. Staying in the fast lane is worth more
    # than the larger candidate pool.
    group_cap = max(1, int(getattr(settings, "openalex_arm_group_terms", 3)))

    def _group_of(items: Sequence[str]) -> str:
        return _or_group(list(items)[:group_cap])

    substrate_words: list[str] = []
    substrate = getattr(req, "substrate", None) if req is not None else None
    if substrate is not None:
        try:
            from ..domain.research_query import SUBSTRATE_VENUE_TERMS

            substrate_words = list(SUBSTRATE_VENUE_TERMS.get(substrate, ()))
        except Exception:
            substrate_words = []

    out: list[ArmQuery] = []
    if settings.openalex_arm_precise:
        out.append(ArmQuery("precise", " ".join(terms[: max(1, cap)]), settings.openalex_arm_weight_precise))
    # The recall/broad arms are substrate-anchored *by construction*: their whole
    # justification is trading the process-side AND collar for an OR while the
    # substrate holds precision. Without a substrate a bare OR measurably loses
    # precision (domain hit rate 100%/28%/29% across three topics, vs 100% for
    # the AND string), so emit the precise arm alone rather than a loose query.
    if substrate_words and settings.openalex_arm_recall:
        out.append(ArmQuery(
            "recall",
            f"{_group_of(substrate_words)} AND {_group_of(terms)}",
            settings.openalex_arm_weight_recall,
        ))
    if substrate_words and settings.openalex_arm_broad:
        venue = [str(v).strip() for v in (getattr(profile, "venue_terms", ()) or ()) if str(v).strip()]
        if venue:
            out.append(ArmQuery(
                "broad",
                f"{_group_of(substrate_words)} AND {_group_of(venue)}",
                settings.openalex_arm_weight_broad,
                conditional=True,
            ))
    return out


def _openalex_work_to_evidence(
    w: dict[str, Any],
    rank_index: int,
    global_offset: int,
    *,
    evidence_source: str = "OpenAlex",
    preferred_source_ids: frozenset[str] | None = None,
    arm: str | None = None,
    arm_delta: float = 0.0,
) -> Evidence:
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    identifier = doi or w.get("id") or ""
    abstract = ""
    inv = w.get("abstract_inverted_index")
    if isinstance(inv, dict):
        pairs: list[tuple[int, str]] = []
        for word, positions in inv.items():
            for pos in positions:
                pairs.append((pos, word))
        abstract = " ".join(w for _, w in sorted(pairs))[:1200]
    oa = w.get("open_access") or {}
    best = w.get("best_oa_location") or {}
    relevance = _ranked(rank_index, global_offset)
    venue_pref_hit = False
    if preferred_source_ids:
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        sid = str(src.get("id") or "")
        short = sid.rsplit("/", 1)[-1] if sid else ""
        if short in preferred_source_ids or sid in preferred_source_ids:
            venue_pref_hit = True
            relevance = round(min(1.0, relevance + 0.15), 3)
    if arm_delta:
        # Tiebreaker only: relevance is a rank-position proxy and the real
        # ordering happens in `_merge_filter_rank`, where it carries 30% of the
        # score. That is enough to stop the broad arm (14k-279k hits) from
        # out-ranking precise hits at the same position.
        relevance = round(min(1.0, max(0.0, relevance + arm_delta)), 3)
    ev = Evidence(
        source=evidence_source,
        identifier=identifier,
        title=w.get("display_name") or "Untitled",
        snippet=abstract[:1200],
        relevance=relevance,
        is_oa=oa.get("is_oa"),
        oa_pdf_url=best.get("pdf_url") or None,
    )
    tags: list[str] = list(ev.domain_tags or [])
    if venue_pref_hit and "venue_pref_hit" not in tags:
        tags.append("venue_pref_hit")
    if arm:
        # Per-arm observability (M4): without this the lottery problem can
        # return unnoticed, since the merged stream looks like any other source.
        tag = f"arm:{arm}"
        if tag not in tags:
            tags.append(tag)
    if tags:
        ev.domain_tags = tags
    return ev


def venue_scoped_query(
    fallback_terms: Sequence[str] | str,
    *,
    req=None,
    profile=None,
    max_terms: int = 6,
) -> str:
    """Boolean query shaped for a *narrow* venue (one journal or repository).

    OpenAlex treats space-separated words as AND **and stems them**. The expanded
    keyword string that works over its 260M-work corpus therefore collapses
    inside a single repository — measured against ChemRxiv (63k works), the
    expanded string returned **1** hit. Three cheaper fixes were tried and
    rejected on live counts:

    * plain OR of the same words — 19,058 hits but off topic, because stemming
      makes "passivation" match "passive tracers" and "conversion" match CO2
      conversion;
    * quoted phrases — *tighter*, not looser (30 hits): a quoted multi-word
      phrase still has to match as a unit;
    * ``keyword_allow`` stems — useless as search terms ("passivat" → 5
      unrelated papers). Hence the separate ``venue_terms`` vocabulary.

    What works is grouping the locked substrate against the domain's process
    vocabulary: ``(magnesium OR AZ91 …) AND (passivation OR conversion …)`` —
    measured 1,057 hits with the top ranks all on topic. Falls back to the
    caller's terms when no substrate or profile is available, so a venue search
    without context degrades to the previous behaviour rather than going empty.
    """
    def _words(items) -> list[str]:
        out: list[str] = []
        for raw in items or ():
            term = str(raw or "").strip()
            # Single tokens only: a phrase inside a venue behaves as an AND of its
            # own words, which is what starves the query in the first place.
            if term and " " not in term and term.isascii():
                out.append(term)
        return list(dict.fromkeys(out))[:max_terms]

    substrate = getattr(req, "substrate", None) if req is not None else None
    substrate_words: list[str] = []
    if substrate is not None:
        try:
            from ..domain.research_query import SUBSTRATE_VENUE_TERMS

            substrate_words = _words(SUBSTRATE_VENUE_TERMS.get(substrate, ()))
        except Exception:
            substrate_words = []

    process_words = _words(getattr(profile, "venue_terms", ()) or ())

    def _group(words: list[str]) -> str:
        return "(" + " OR ".join(words) + ")"

    if substrate_words and process_words:
        return f"{_group(substrate_words)} AND {_group(process_words)}"
    if substrate_words or process_words:
        return _group(substrate_words or process_words)
    # No structured context (e.g. an ad-hoc search with no requirement): keep the
    # caller's terms verbatim so behaviour is unchanged from before this existed.
    if isinstance(fallback_terms, str):
        return fallback_terms
    return " ".join(str(t) for t in fallback_terms or () if t)


def search_openalex(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    settings: Settings | None = None,
    domain=None,
    source_id: str | None = None,
    evidence_source: str = "OpenAlex",
    preferred_source_ids: tuple[str, ...] | list[str] | None = None,
    taxonomy_source: str = "openalex",
    arm: str | None = None,
    arm_delta: float = 0.0,
) -> list[Evidence]:
    """OpenAlex works search (requires mailto for polite pool).

    ``source_id`` (e.g. ChemRxiv ``S4393918830``) appends
    ``primary_location.source.id`` to the filter. ``preferred_source_ids``
    soft-boosts matching venues without hard-filtering.
    """
    settings = settings or get_settings()
    if not settings.openalex_enabled:
        return []
    q = (query or "").strip()
    if not q:
        return []
    if limit <= 0:
        return []
    base_params: dict[str, Any] = {"search": q, "per-page": 25}
    filter_parts: list[str] = []
    preferred: frozenset[str] = frozenset()
    # P0: DomainSearchProfile → OpenAlex concepts.id filter (degrades gracefully).
    try:
        from ..domain.search_profiles import (
            openalex_concepts_filter,
            openalex_join_filters,
            resolve_profile,
        )
        _prof = resolve_profile(domain)
        if getattr(settings, "openalex_concept_filter", True):
            _cf = openalex_concepts_filter(_prof) if _prof is not None else ""
            if _cf:
                filter_parts.append(_cf)
        if preferred_source_ids:
            preferred = frozenset(str(x).strip() for x in preferred_source_ids if x and str(x).strip())
        elif _prof is not None and not source_id:
            preferred = frozenset(
                str(x).strip()
                for x in (_prof.preferred_openalex_source_ids or ())
                if x and str(x).strip()
            )
        if source_id:
            sid = str(source_id).strip()
            if sid.startswith("https://openalex.org/"):
                sid = sid.rsplit("/", 1)[-1]
            if sid:
                filter_parts.append(f"primary_location.source.id:{sid}")
        joined = openalex_join_filters(*filter_parts)
        if joined:
            base_params["filter"] = joined
    except Exception:
        if source_id:
            sid = str(source_id).strip().rsplit("/", 1)[-1]
            if sid:
                base_params["filter"] = f"primary_location.source.id:{sid}"
    if effective_setting(settings, "openalex_mailto"):
        base_params["mailto"] = effective_setting(settings, "openalex_mailto")
    # Ride the keyed budget, not the free tier. The mailto-only tier is 1,000
    # requests/day and then answers `429 Insufficient budget` — measured
    # 2026-09-11: the identical query returned 200 with the key and 429 with only
    # mailto, with `Retry-After: 60264`. The arm design multiplies requests per
    # search (2-3 arms × pages), so search has to be on the key ($0.001/request)
    # rather than the shared free pool.
    _api_key = effective_setting(settings, "openalex_api_key")
    if _api_key:
        base_params["api_key"] = _api_key
    try:
        out: list[Evidence] = []
        page = 1 + offset // 25
        skip_in_page = offset % 25
        global_idx = 0
        with httpx.Client(timeout=_TIMEOUT_SEC) as client:
            while len(out) < limit:
                params = {**base_params, "page": page}
                resp = _get_with_retry(client, "https://api.openalex.org/works", params)
                resp.raise_for_status()
                results = resp.json().get("results") or []
                if not results:
                    break
                page_added = 0
                for w in results[skip_in_page:]:
                    page_added += 1
                    # 强制过滤无 OA 文献：无法下载全文的资料源不进检索结果。
                    oa = w.get("open_access") or {}
                    if not oa.get("is_oa"):
                        continue
                    out.append(
                        _openalex_work_to_evidence(
                            w,
                            global_idx,
                            offset,
                            evidence_source=evidence_source,
                            preferred_source_ids=preferred or None,
                            arm=arm,
                            arm_delta=arm_delta,
                        )
                    )
                    global_idx += 1
                    if len(out) >= limit:
                        break
                if page_added == 0 or len(results) < 25:
                    break
                skip_in_page = 0
                page += 1
        if domain is not None:
            from .domain_tagging import tag_evidence_domain
            tax = taxonomy_source if taxonomy_source in {
                "arxiv", "openalex", "chemrxiv", "cpc", "lexical", "none"
            } else "openalex"
            for _ev in out:
                tag_evidence_domain(_ev, domain, taxonomy_source=tax, match="strong")  # type: ignore[arg-type]
        return out
    except Exception as exc:
        return degrade_return(logger, exc, "OpenAlex search failed", [])


def _serpapi_search(
    engine: str,
    query: str,
    api_key: str,
    limit: int,
    offset: int,
    extra_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "engine": engine,
        "q": query,
        "api_key": api_key,
        "num": min(20, limit + offset),
    }
    if extra_params:
        params.update(extra_params)
    with httpx.Client(timeout=_TIMEOUT_SEC) as client:
        resp = client.get("https://serpapi.com/search.json", params=params)
        resp.raise_for_status()
        return resp.json()


def search_serpapi_scholar(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    settings: Settings | None = None,
) -> list[Evidence]:
    """Google Scholar via SerpAPI."""
    settings = settings or get_settings()
    key = effective_setting(settings, "serpapi_api_key")
    q = (query or "").strip()
    if not key or not q:
        return []
    try:
        data = _serpapi_search("google_scholar", q, key, limit, offset)
        items = data.get("organic_results") or []
        out: list[Evidence] = []
        for i, r in enumerate(items[offset : offset + limit]):
            out.append(
                Evidence(
                    source="SerpAPI Scholar",
                    identifier=r.get("result_id") or r.get("link") or "",
                    title=r.get("title") or "Untitled",
                    snippet=(r.get("snippet") or "")[:1200],
                    relevance=_ranked(i, offset),
                )
            )
        return out
    except Exception as exc:
        return degrade_return(logger, exc, "SerpAPI Scholar search failed", [])


def search_serpapi_patents(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    settings: Settings | None = None,
    hl: str = "en",
    source_label: str = "Google Patents",
    domain=None,
    cpc_prefixes: tuple[str, ...] | list[str] | None = None,
) -> list[Evidence]:
    """Google Patents via SerpAPI (English or Chinese query)."""
    settings = settings or get_settings()
    key = effective_setting(settings, "serpapi_api_key")
    q = (query or "").strip()
    if not key or not q:
        return []
    # P0: append CPC=(…) from DomainSearchProfile when patent_cpc_filter is on.
    try:
        from ..domain.search_profiles import cpc_query_clause, resolve_profile
        if getattr(settings, "patent_cpc_filter", True):
            _prof = resolve_profile(domain)
            if _prof is not None:
                _clause = cpc_query_clause(_prof)
                if _clause and _clause not in q:
                    q = f"{q} {_clause}"
            elif cpc_prefixes:
                _clause = "CPC=(" + " OR ".join(
                    x.strip().upper() for x in cpc_prefixes if x and x.strip()
                ) + ")"
                if _clause not in q:
                    q = f"{q} {_clause}"
    except Exception:
        pass
    try:
        data = _serpapi_search(
            "google_patents",
            q,
            key,
            limit,
            offset,
            extra_params={"hl": hl},
        )
        items = data.get("organic_results") or data.get("patents") or []
        out: list[Evidence] = []
        for i, r in enumerate(items[offset : offset + limit]):
            pub = r.get("publication_number") or r.get("patent_id") or ""
            out.append(
                Evidence(
                    source=source_label,
                    identifier=str(pub),
                    title=r.get("title") or "Untitled patent",
                    snippet=(r.get("snippet") or r.get("abstract") or "")[:400],
                    relevance=_ranked(i, offset),
                )
            )
        if domain is not None:
            from .domain_tagging import tag_evidence_domain
            for _ev in out:
                tag_evidence_domain(
                    _ev, domain, taxonomy_source="cpc", match="weak", extra_tags=["cpc_filter"]
                )
        return out
    except Exception as exc:
        logger.warning("SerpAPI Google Patents failed (%s): %s", hl, exc)
        return []


def search_serpapi_chain(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    settings: Settings | None = None,
    prefer: str = "scholar",
) -> list[Evidence]:
    """SerpAPI priority chain: scholar → google_patents on empty scholar results."""
    settings = settings or get_settings()
    if prefer == "patents":
        hits = search_serpapi_patents(query, limit, offset, settings=settings)
        if hits:
            return hits
        return search_serpapi_scholar(query, limit, offset, settings=settings)
    hits = search_serpapi_scholar(query, limit, offset, settings=settings)
    if hits:
        return hits
    return search_serpapi_patents(query, limit, offset, settings=settings)


def search_tavily(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    settings: Settings | None = None,
    topic: str = "general",
) -> list[Evidence]:
    """Tavily semantic web search."""
    settings = settings or get_settings()
    key = effective_setting(settings, "tavily_api_key")
    q = (query or "").strip()
    if not key or not q:
        return []
    try:
        with httpx.Client(timeout=_TIMEOUT_SEC) as client:
            resp = client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": q,
                    "max_results": min(20, limit + offset),
                    "topic": topic,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        results = payload.get("results") or []
        out: list[Evidence] = []
        for i, r in enumerate(results[offset : offset + limit]):
            out.append(
                Evidence(
                    source="Tavily",
                    identifier=r.get("url") or "",
                    title=r.get("title") or "Untitled",
                    snippet=(r.get("content") or "")[:600],
                    relevance=_ranked(i, offset),
                )
            )
        return out
    except Exception as exc:
        return degrade_return(logger, exc, "Tavily search failed", [])


def search_serpapi_web(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    settings: Settings | None = None,
) -> list[Evidence]:
    """General web search via SerpAPI's Google engine.

    SerpAPI was already wired for scholar and patents but not for plain web, so
    a deployment holding a SerpAPI key still dropped to the keyless DuckDuckGo
    tier for general queries — the tier whose result quality prompted this.
    """
    settings = settings or get_settings()
    key = effective_setting(settings, "serpapi_api_key")
    q = (query or "").strip()
    if not key or not q:
        return []
    try:
        payload = _serpapi_search("google", q, key, limit, offset)
        results = (payload.get("organic_results") or [])[offset : offset + limit]
        return [
            Evidence(
                # "(web)" is load-bearing, following the existing `CNIPA (web)`
                # convention: a bare "SerpAPI" is already classified as
                # patent/literature (SerpAPI also backs scholar and patent
                # search), and `_is_patent_or_literature` returns False for
                # anything `_is_weblike` claims first. Without the suffix these
                # general-web hits would be treated as authoritative literature
                # and skip web-specific near-dup filtering.
                source="SerpAPI (web)",
                identifier=r.get("link") or "",
                title=r.get("title") or "Untitled",
                snippet=(r.get("snippet") or "")[:600],
                relevance=_ranked(i, offset),
            )
            for i, r in enumerate(results)
            if r.get("link")
        ]
    except Exception as exc:
        return degrade_return(logger, exc, "SerpAPI web search failed", [])


def search_google_patents_cn(
    chinese_query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    settings: Settings | None = None,
) -> list[Evidence]:
    """Chinese patent retrieval via Google Patents + Chinese query (SerpAPI)."""
    q = (chinese_query or "").strip()
    if not q:
        return []
    return search_serpapi_patents(
        q,
        limit,
        offset,
        settings=settings,
        hl="zh-CN",
        source_label="Google Patents CN",
    )


def search_cnipa_parallel(
    chinese_query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    settings: Settings | None = None,
) -> list[Evidence]:
    """CNIPA parallel route (v1): second Chinese query via Tavily/SerpAPI web, not direct CNIPA API."""
    settings = settings or get_settings()
    q = (chinese_query or "").strip()
    if not q:
        return []
    cn_query = f"{q} 中国专利 CNIPA site:cnipa.gov.cn OR site:patent.gov.cn"
    if effective_setting(settings, "tavily_api_key"):
        hits = search_tavily(cn_query, limit, offset, settings=settings, topic="general")
        if hits:
            return [
                Evidence(
                    source="CNIPA (web)",
                    identifier=e.identifier,
                    title=e.title,
                    snippet=e.snippet,
                    relevance=e.relevance,
                )
                for e in hits
            ]
    if effective_setting(settings, "serpapi_api_key"):
        try:
            data = _serpapi_search(
                "google",
                cn_query,
                effective_setting(settings, "serpapi_api_key"),
                limit,
                offset,
                extra_params={"hl": "zh-CN", "gl": "cn"},
            )
            items = data.get("organic_results") or []
            out: list[Evidence] = []
            for i, r in enumerate(items[offset : offset + limit]):
                out.append(
                    Evidence(
                        source="CNIPA (web)",
                        identifier=r.get("link") or "",
                        title=r.get("title") or "Untitled",
                        snippet=(r.get("snippet") or "")[:500],
                        relevance=_ranked(i, offset),
                    )
                )
            return out
        except Exception as exc:
            logger.warning("CNIPA parallel SerpAPI web failed: %s", exc)
    return []
