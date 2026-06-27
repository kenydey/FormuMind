"""External search providers — OpenAlex, SerpAPI, Tavily, Google Patents CN, CNIPA parallel.

Each function returns normalized :class:`~app.domain.schemas.Evidence` lists and
swallows network errors (logs + returns []).
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ..config import Settings, get_settings
from ..domain.schemas import Evidence

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 25.0
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


def search_openalex(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    settings: Settings | None = None,
) -> list[Evidence]:
    """OpenAlex works search (requires mailto for polite pool)."""
    settings = settings or get_settings()
    if not settings.openalex_enabled:
        return []
    q = (query or "").strip()
    if not q:
        return []
    params: dict[str, Any] = {
        "search": q,
        "per-page": min(25, limit + offset),
        "page": 1 + offset // 25,
    }
    if settings.openalex_mailto:
        params["mailto"] = settings.openalex_mailto
    try:
        with httpx.Client(timeout=_TIMEOUT_SEC) as client:
            resp = client.get("https://api.openalex.org/works", params=params)
            resp.raise_for_status()
            payload = resp.json()
        results = payload.get("results") or []
        out: list[Evidence] = []
        for i, w in enumerate(results[offset % 25 : offset % 25 + limit]):
            doi = (w.get("doi") or "").replace("https://doi.org/", "")
            identifier = doi or w.get("id") or ""
            abstract = ""
            inv = w.get("abstract_inverted_index")
            if isinstance(inv, dict):
                # Reconstruct abstract from inverted index (OpenAlex format)
                pairs: list[tuple[int, str]] = []
                for word, positions in inv.items():
                    for pos in positions:
                        pairs.append((pos, word))
                abstract = " ".join(w for _, w in sorted(pairs))[:1200]
            out.append(
                Evidence(
                    source="OpenAlex",
                    identifier=identifier,
                    title=w.get("display_name") or "Untitled",
                    snippet=abstract[:1200],
                    relevance=_ranked(i, offset),
                )
            )
        return out
    except Exception as exc:
        logger.warning("OpenAlex search failed: %s", exc)
        return []


def _serpapi_search(
    engine: str,
    query: str,
    api_key: str,
    limit: int,
    offset: int,
    extra_params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
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
    key = settings.serpapi_api_key
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
        logger.warning("SerpAPI Scholar search failed: %s", exc)
        return []


def search_serpapi_patents(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    settings: Settings | None = None,
    hl: str = "en",
    source_label: str = "Google Patents",
) -> list[Evidence]:
    """Google Patents via SerpAPI (English or Chinese query)."""
    settings = settings or get_settings()
    key = settings.serpapi_api_key
    q = (query or "").strip()
    if not key or not q:
        return []
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
    key = settings.tavily_api_key
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
        logger.warning("Tavily search failed: %s", exc)
        return []


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
    if settings.tavily_api_key:
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
    if settings.serpapi_api_key:
        try:
            data = _serpapi_search(
                "google",
                cn_query,
                settings.serpapi_api_key,
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
