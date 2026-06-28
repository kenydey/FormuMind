"""Patent & literature intelligence service.

When ``patent_client`` / ``paper-qa`` are installed and configured, this module
fetches real patents from USPTO/EPO. Otherwise it serves a curated offline seed
corpus of representative patent/literature abstracts for the three product
domains, so research always returns cited evidence.
"""
from __future__ import annotations

import concurrent.futures
import logging
import re

from ..domain.schemas import Evidence, ProductDomain, Requirement

logger = logging.getLogger(__name__)

# Per-source network ceiling — prevents one hung API from blocking the whole search.
_SOURCE_TIMEOUT_SEC = 25

# Curated seed corpus — representative, paraphrased abstracts used offline.
SEED_CORPUS: dict[ProductDomain, list[dict]] = {
    ProductDomain.anticorrosion_coating: [
        {"identifier": "US9982145B2", "source": "USPTO", "title": "Waterborne epoxy anticorrosive coating with zinc phosphate",
         "snippet": "A two-component waterborne epoxy primer containing 4-10 wt% zinc phosphate achieves >500 h neutral salt spray on cold-rolled steel at film weights of 60-80 g/m^2."},
        {"identifier": "EP3211048A1", "source": "EPO", "title": "Low-temperature curing anticorrosive primer",
         "snippet": "An acrylic-polyurethane hybrid binder cured below 60 C with cerium-based inhibitors delivers improved edge corrosion protection and adhesion on galvanized steel."},
        {"identifier": "US10465093B2", "source": "USPTO", "title": "Zinc-rich epoxy with lamellar pigments",
         "snippet": "Combining 70-85 wt% zinc dust with lamellar talc reduces permeability; cathodic protection extends salt-spray endurance beyond 1000 h."},
        {"identifier": "DOI:10.1016/j.porgcoat.2019.105338", "source": "literature", "title": "MBT-doped epoxy coatings",
         "snippet": "2-Mercaptobenzothiazole at 1-3 wt% provides active inhibition by chemisorption on iron, complementing barrier protection."},
    ],
    ProductDomain.degreaser: [
        {"identifier": "US8569221B2", "source": "USPTO", "title": "Alkaline cleaning composition for metal surfaces",
         "snippet": "An alkaline builder blend of metasilicate and tripolyphosphate with nonionic surfactant removes >95% mineral oil at pH 12-13 and 50 C."},
        {"identifier": "EP2576743B1", "source": "EPO", "title": "Low-foam metal degreaser",
         "snippet": "Selecting EO/PO block surfactants below their cloud point gives high oil emulsification with low foam in spray cleaning."},
        {"identifier": "DOI:10.1080/01932691.2018.1455522", "source": "literature", "title": "Limonene microemulsion cleaners",
         "snippet": "D-limonene microemulsions with nonionic coupling solvents clean polar and non-polar soils near neutral pH with reduced VOC."},
    ],
    ProductDomain.surface_treatment: [
        {"identifier": "US7510612B2", "source": "USPTO", "title": "Chrome-free conversion coating for aluminum",
         "snippet": "A hexafluorozirconic acid bath with organosilane forms a thin Zr/Si conversion film, improving paint adhesion and filiform resistance without hexavalent chromium."},
        {"identifier": "EP1633905B1", "source": "EPO", "title": "Zinc phosphating with nitrite accelerator",
         "snippet": "Zinc/manganese phosphating accelerated by nitrite yields fine-crystalline coatings of 1.5-3 g/m^2 with excellent paint adhesion on steel."},
        {"identifier": "DOI:10.1016/j.surfcoat.2017.06.001", "source": "literature", "title": "Cerium-based passivation",
         "snippet": "Cerium nitrate post-treatment precipitates cerium oxide/hydroxide at cathodic sites, inhibiting corrosion on aluminum alloys."},
    ],
}

# Identifiers that belong to the offline seed corpus — used to tell offline
# fallback evidence apart from real online hits (online results are never filtered).
_SEED_IDENTIFIERS = {d["identifier"] for docs in SEED_CORPUS.values() for d in docs}

# Bilingual keyword tokenizer: English words by run, Chinese by single char.
_KW_RE = re.compile(r"[a-z0-9]+|[一-鿿]")


def _keywords(text: str) -> set[str]:
    return set(_KW_RE.findall(text.lower()))


def _filter_seed_by_query(
    seeds: list[Evidence], query: str, min_keep: int = 2
) -> list[Evidence]:
    """Keep seed entries whose title+snippet share a keyword with the query.

    Falls back to the ``min_keep`` highest-relevance entries when nothing matches,
    so offline research always returns some cited evidence.
    """
    q_kw = _keywords(query)
    if not q_kw or not seeds:
        return seeds
    matched = [e for e in seeds if q_kw & _keywords(f"{e.title} {e.snippet}")]
    if matched:
        return matched
    return sorted(seeds, key=lambda x: x.relevance, reverse=True)[:min_keep]


def _resolve_search_query(query: str) -> str:
    """Expand a user topic via QueryExpander for cross-lingual retrieval."""
    from .deep_research.query_expander import prepare_search_queries

    return prepare_search_queries(query).rank_q


def _prepare_search_queries(query: str):
    """Return SearchQueries bundle for multi-source search."""
    from .deep_research.query_expander import prepare_search_queries

    return prepare_search_queries(query)


def _fetch_with_timeout(fetch, cursor: int) -> list[Evidence]:
    """Run one source page fetch with a hard timeout."""
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fetch, cursor)
    try:
        return fut.result(timeout=_SOURCE_TIMEOUT_SEC) or []
    except Exception as exc:
        logger.warning("source fetch timed out or failed: %s", exc)
        return []
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _build_patent_query(req: Requirement | None, query: str) -> str:
    """Combine user search box text with requirement headline for patent retrieval."""
    parts = [p for p in [query.strip(), req.headline() if req else ""] if p]
    return " ".join(dict.fromkeys(parts))


def _online_search(
    req: Requirement | None,
    query: str,
    limit: int,
    *,
    ipc_codes: tuple[str, ...] | list[str] | None = None,
) -> list[Evidence] | None:
    """Attempt real patent retrieval; return None if unavailable."""
    try:
        from patent_client import Patent  # type: ignore
    except Exception:
        return None
    try:
        search_q = _build_patent_query(req, query)
        if ipc_codes:
            search_q = f"{search_q} {' '.join(ipc_codes[:3])}".strip()
        results = Patent.objects.filter(search_q).limit(limit)  # pragma: no cover - network
        evidence = []
        for i, p in enumerate(results):
            evidence.append(Evidence(
                source="USPTO", identifier=str(getattr(p, "publication_number", f"P{i}")),
                title=str(getattr(p, "title", "")), snippet=str(getattr(p, "abstract", ""))[:400],
                relevance=max(0.1, 1.0 - i * 0.02),
            ))
        return evidence or None
    except Exception as exc:  # pragma: no cover - network/credentials
        logger.warning("patent_client online search failed: %s", exc)
        return None


def _search_epo_patents(
    query: str,
    ipc_codes: tuple[str, ...] | list[str] | None,
    limit: int,
    offset: int = 0,
) -> list[Evidence]:
    """EPO Inpadoc search with optional CPC class filter."""
    from ..config import get_settings

    settings = get_settings()
    if not settings.epo_consumer_key or not settings.epo_consumer_secret:
        return []
    try:
        from patent_client import Inpadoc  # type: ignore
        from ..services.secrets_store import sync_secrets_to_os_environ

        sync_secrets_to_os_environ(settings)
        filters: dict = {}
        if query.strip():
            filters["title_and_abstract"] = query.strip()
        codes = list(ipc_codes or [])[:2]
        if codes:
            filters["cpc_class"] = codes[0]
        if not filters:
            return []
        results = Inpadoc.objects.filter(**filters).limit(limit + offset)  # pragma: no cover
        out: list[Evidence] = []
        for i, p in enumerate(results):
            out.append(
                Evidence(
                    source="EPO",
                    identifier=str(getattr(p, "publication_number", getattr(p, "epodoc_publication", f"EP{i}"))),
                    title=str(getattr(p, "title", "") or getattr(p, "patent_title", "")),
                    snippet=str(getattr(p, "abstract", "") or "")[:400],
                    relevance=round(max(0.1, 1.0 - (offset + i) * 0.02), 3),
                )
            )
        return out[offset : offset + limit]
    except Exception as exc:
        logger.warning("EPO Inpadoc search failed: %s", exc)
        return []


def search(req: Requirement, limit: int = 8, query: str = "") -> list[Evidence]:
    """Backward-compatible public entry point — delegates to search_patents."""
    return search_patents(req, query=query, limit=limit)


def search_patents(
    req: Requirement,
    limit: int = 5,
    offset: int = 0,
    query: str = "",
    *,
    ipc_codes: tuple[str, ...] | list[str] | None = None,
    chinese_query: str = "",
) -> list[Evidence]:
    """专利搜索（EPO + USPTO + Google Patents + 中文专利并行，种子语料回退）。"""
    from ..config import get_settings
    from .search_providers import (
        merge_patent_evidence,
        search_cnipa_parallel,
        search_google_patents_cn,
        search_serpapi_patents,
    )

    patent_q = query or _build_patent_query(req, "")
    want = limit + offset
    settings = get_settings()
    batches: list[list[Evidence]] = [
        _search_epo_patents(patent_q, ipc_codes, want, 0),
    ]
    us = _online_search(req, patent_q, want, ipc_codes=ipc_codes)
    if us:
        batches.append(us)
    if settings.serpapi_api_key:
        batches.append(search_serpapi_patents(patent_q, want, 0, settings=settings))
    cq = (chinese_query or "").strip()
    if cq:
        batches.append(search_google_patents_cn(cq, want, 0, settings=settings))
        batches.append(search_cnipa_parallel(cq, want, 0, settings=settings))
    merged = merge_patent_evidence(*batches, limit=want)
    if merged:
        return merged[offset : offset + limit]
    corpus = SEED_CORPUS.get(req.domain, [])
    seed_query = query or _build_patent_query(req, "")
    evidence = [
        Evidence(relevance=round(max(0.4, 1.0 - i * 0.08), 2), **doc)
        for i, doc in enumerate(corpus)
    ]
    filtered = _filter_seed_by_query(evidence, seed_query)
    return filtered[offset : offset + limit]


def search_patents_by_query(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    ipc_codes: tuple[str, ...] | list[str] | None = None,
    chinese_query: str = "",
) -> list[Evidence]:
    """仅按用户 query 检索专利（无 Requirement 时使用种子语料回退）。"""
    from ..config import get_settings
    from .search_providers import (
        merge_patent_evidence,
        search_cnipa_parallel,
        search_google_patents_cn,
        search_serpapi_patents,
    )

    want = limit + offset
    settings = get_settings()
    batches: list[list[Evidence]] = [
        _search_epo_patents(query, ipc_codes, want, 0),
    ]
    us = _online_search(None, query, want, ipc_codes=ipc_codes)
    if us:
        batches.append(us)
    if settings.serpapi_api_key:
        batches.append(search_serpapi_patents(query, want, 0, settings=settings))
    cq = (chinese_query or "").strip()
    if cq:
        batches.append(search_google_patents_cn(cq, want, 0, settings=settings))
        batches.append(search_cnipa_parallel(cq, want, 0, settings=settings))
    merged = merge_patent_evidence(*batches, limit=want)
    if merged:
        return merged[offset : offset + limit]
    all_seeds: list[Evidence] = []
    for domain_docs in SEED_CORPUS.values():
        for i, doc in enumerate(domain_docs):
            if doc.get("source") in ("USPTO", "EPO"):
                all_seeds.append(
                    Evidence(relevance=round(max(0.4, 1.0 - i * 0.08), 2), **doc)
                )
    filtered = _filter_seed_by_query(all_seeds, query)
    return filtered[offset : offset + limit]


def search_arxiv(query: str, limit: int = 5, offset: int = 0, *, domain_filter: bool | None = None) -> list[Evidence]:
    """arXiv 学术预印本搜索（arxiv 库）。``offset`` 支持增量翻页。"""
    from ..config import get_settings

    settings = get_settings()
    if not settings.arxiv_search_enabled:
        return []
    use_filter = domain_filter if domain_filter is not None else settings.arxiv_domain_filter
    arxiv_query = query
    if use_filter and query.strip():
        arxiv_query = (
            f"({query}) AND (cat:cond-mat.mtrl-sci OR cat:physics.chem-ph OR cat:cs.CE)"
        )
    try:
        import arxiv  # type: ignore

        client = arxiv.Client(page_size=50, delay_seconds=3, num_retries=1)
        results = list(
            client.results(
                arxiv.Search(
                    query=arxiv_query,
                    max_results=limit + offset,
                    sort_by=arxiv.SortCriterion.Relevance,
                )
            )
        )[offset : offset + limit]
        return [
            Evidence(
                source="arXiv",
                identifier=r.entry_id,
                title=r.title,
                snippet=(r.summary or "")[:500],
                relevance=round(max(0.1, 1.0 - (offset + i) * 0.02), 3),
            )
            for i, r in enumerate(results)
        ]
    except Exception as exc:
        logger.warning("arXiv search failed: %s", exc)
        return []


_ALLOWED_S2_FIELDS = frozenset({
    "chemistry",
    "materials science",
    "engineering",
    "environmental science",
    "medicine",
})


def search_semantic_scholar(query: str, limit: int = 5, offset: int = 0) -> list[Evidence]:
    """Semantic Scholar 学术文献搜索（HTTP API + 超时，避免 SDK 挂死）。"""
    try:
        import httpx

        want = min(100, (limit + offset) * 3)
        with httpx.Client(timeout=_SOURCE_TIMEOUT_SEC) as client:
            resp = client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": query,
                    "limit": want,
                    "fields": "title,abstract,externalIds,paperId,fieldsOfStudy",
                },
                headers={"User-Agent": "FormuMind/0.1 (research platform)"},
            )
            resp.raise_for_status()
            payload = resp.json()
        papers = payload.get("data") or []
        filtered = []
        for p in papers:
            fos = {str(x).lower() for x in (p.get("fieldsOfStudy") or [])}
            if fos and not (fos & _ALLOWED_S2_FIELDS):
                continue
            filtered.append(p)
        papers = filtered[offset : offset + limit]
        out: list[Evidence] = []
        for i, p in enumerate(papers):
            ext = p.get("externalIds") or {}
            identifier = ext.get("DOI") or p.get("paperId") or ""
            out.append(
                Evidence(
                    source="Semantic Scholar",
                    identifier=identifier,
                    title=p.get("title") or "Untitled",
                    snippet=(p.get("abstract") or "")[:1200],
                    relevance=round(max(0.1, 1.0 - (offset + i) * 0.02), 3),
                )
            )
        return out
    except Exception as exc:
        logger.warning("Semantic Scholar search failed: %s", exc)
        return []


def search_openalex(query: str, limit: int = 5, offset: int = 0) -> list[Evidence]:
    """OpenAlex 学术文献（需 mailto 礼貌池，可在 config 关闭）。"""
    from .search_providers import search_openalex as _openalex

    return _openalex(query, limit, offset)


def search_serpapi_literature(query: str, limit: int = 5, offset: int = 0) -> list[Evidence]:
    """SerpAPI Scholar → Google Patents 优先链（文献向）。"""
    from .search_providers import search_serpapi_chain

    return search_serpapi_chain(query, limit, offset, prefer="scholar")


def search_internet(query: str, limit: int = 5, offset: int = 0) -> list[Evidence]:
    """互联网检索：Tavily 优先，DuckDuckGo 回退。"""
    from ..config import get_settings
    from .search_providers import search_tavily

    settings = get_settings()
    if settings.tavily_api_key:
        hits = search_tavily(query, limit, offset, settings=settings)
        if hits:
            return hits
    return search_web(query, limit, offset)


def search_web(query: str, limit: int = 5, offset: int = 0) -> list[Evidence]:
    """DuckDuckGo 互联网搜索（ddgs，无需 API key）。``offset`` 支持增量翻页。"""
    try:
        try:
            from ddgs import DDGS  # type: ignore  # 新包名
        except ImportError:
            from duckduckgo_search import DDGS  # type: ignore  # 旧包兜底（向后兼容）
        results = list(DDGS().text(query, max_results=limit + offset))[offset : offset + limit]
        return [
            Evidence(
                source="Internet",
                identifier=r.get("href") or r.get("url") or "",  # ddgs 新版可能用 url
                title=r.get("title", ""),
                snippet=(r.get("body") or "")[:500],
                relevance=round(max(0.1, 1.0 - (offset + i) * 0.02), 3),
            )
            for i, r in enumerate(results)
        ]
    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return []


def _is_patent_or_literature(e: Evidence) -> bool:
    s = (e.source or "").lower()
    if _is_weblike(e):
        return False
    return any(
        k in s
        for k in (
            "uspto", "epo", "patent", "arxiv", "semantic", "literature",
            "chemcrow", "openalex", "doi", "serpapi", "tavily", "cnipa", "google patents",
        )
    )


def _overlap(e: Evidence, q_kw: set[str]) -> int:
    """Number of query keywords appearing in an evidence's title+snippet."""
    if not q_kw:
        return 1
    return len(q_kw & _keywords(f"{e.title} {e.snippet}"))


def _rank_score(e: Evidence, q_kw: set[str]) -> tuple[float, float]:
    overlap = _overlap(e, q_kw)
    overlap_norm = overlap / max(1, len(q_kw)) if q_kw else 0.0
    return (overlap_norm * 0.7 + e.relevance * 0.3, e.relevance)


def _is_weblike(e: Evidence) -> bool:
    """Internet/web sources are the junk-prone ones worth filtering hard."""
    s = (e.source or "").lower()
    return (
        "internet" in s
        or "web" in s
        or "duck" in s
        or "tavily" in s
        or "cnipa" in s
    )


def _merge_filter_rank(
    results: list[Evidence], query: str, total_limit: int
) -> list[Evidence]:
    """Filter junk, dedupe, rank by relevance, and cap to ``total_limit``.

    Relevance/junk rules:
    * Offline seed corpus → kept via :func:`_filter_seed_by_query` (query-matched,
      else a couple of top entries so research is never empty).
    * Internet/web hits with zero query-keyword overlap are dropped as junk.
    * Patents / literature require at least one keyword overlap (unless empty query).
    """
    q_kw = _keywords(query)
    seeds = [e for e in results if e.identifier in _SEED_IDENTIFIERS]
    online = [e for e in results if e.identifier not in _SEED_IDENTIFIERS]

    def _keep(e: Evidence) -> bool:
        if not q_kw:
            return True
        ov = _overlap(e, q_kw)
        if _is_weblike(e):
            return ov > 0
        if _is_patent_or_literature(e):
            return ov >= 1
        return ov > 0

    filtered_online = [e for e in online if _keep(e)]
    merged = filtered_online + _filter_seed_by_query(seeds, query)

    seen: set[str] = set()
    deduped: list[Evidence] = []
    for e in sorted(merged, key=lambda x: _rank_score(x, q_kw), reverse=True):
        key = e.identifier or e.title
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    return deduped[:total_limit]


def _build_streams(
    patent_query: str,
    western_query: str,
    source_types: list[str],
    req: Requirement | None,
    page_size: int,
    *,
    ipc_codes: tuple[str, ...] | list[str] = (),
    chinese_query: str = "",
) -> list[dict]:
    """One paged stream per source. ``paged`` sources support offset/round paging;
    single-shot sources (chemcrow/notebooklm) yield once then finish."""
    streams: list[dict] = []

    def add(name: str, fetch, paged: bool) -> None:
        streams.append({"name": name, "fetch": fetch, "cursor": 0, "paged": paged, "done": False})

    if "patents" in source_types:
        ipc = tuple(ipc_codes)
        if req is not None:
            add(
                "patents",
                lambda off, q=patent_query, ipc=ipc, cq=chinese_query: search_patents(
                    req, page_size, offset=off, query=q, ipc_codes=ipc, chinese_query=cq
                ),
                True,
            )
        else:
            add(
                "patents",
                lambda off, q=patent_query, ipc=ipc, cq=chinese_query: search_patents_by_query(
                    q, page_size, offset=off, ipc_codes=ipc, chinese_query=cq
                ),
                True,
            )
    if "literature" in source_types:
        from ..config import get_settings

        lit_settings = get_settings()
        add(
            "serpapi_lit",
            lambda off, q=western_query: search_serpapi_literature(q, page_size, offset=off),
            True,
        )
        if lit_settings.openalex_enabled:
            add("openalex", lambda off, q=western_query: search_openalex(q, page_size, offset=off), True)
        if lit_settings.arxiv_search_enabled:
            add("arxiv", lambda off, q=western_query: search_arxiv(q, page_size, offset=off), True)
        add(
            "s2",
            lambda off, q=western_query: search_semantic_scholar(q, page_size, offset=off),
            True,
        )
        add("chemlit", lambda off, q=western_query: search_chemcrow_lit(q, page_size), False)
    if "internet" in source_types:
        web_q = chinese_query or western_query
        add("internet", lambda off, q=web_q: search_internet(q, page_size, offset=off), True)
        add("chemweb", lambda off, q=western_query: search_chemcrow_web(q, page_size), False)
    if "notebooklm" in source_types:
        def _nb(off: int, q=western_query) -> list[Evidence]:
            from .notebooklm import search_notebooklm  # 延迟导入：未装库时零开销

            return search_notebooklm(q, page_size)

        add("notebooklm", _nb, False)
    return streams


def iter_search(
    query: str,
    source_types: list[str],
    req: Requirement | None = None,
    total_limit: int = 300,
    per_source_cap: int = 50,
    max_rounds: int = 20,
    progress_cb=None,
) -> list[Evidence]:
    """Incremental multi-source retrieval — fetch in rounds until no source turns
    up new related results (no fixed time budget).

    Each round pulls the next page from every still-active source concurrently.
    ``progress_cb`` (if given) is invoked after **each source** completes (not only
    at round end), so the UI can render results while the search keeps going.
    """
    q = query or (req.headline() if req else "coating formulation")
    if (query or "").strip():
        sq = _prepare_search_queries(q)
        rank_q = sq.rank_q
        patent_q = sq.patent_q
        western_q = sq.western_q
        chinese_q = sq.chinese_q
        ipc_codes = sq.ipc_codes
    else:
        rank_q = patent_q = western_q = chinese_q = q
        ipc_codes = ()
    page_size = max(1, min(per_source_cap, 50))
    streams = _build_streams(
        patent_q, western_q, source_types, req, page_size,
        ipc_codes=ipc_codes, chinese_query=chinese_q,
    )

    raw: list[Evidence] = []
    seen_ids: set[str] = set()
    rounds = 0

    def _notify(*, source: str | None = None, new_count: int = 0) -> None:
        if progress_cb is None:
            return
        ranked = _merge_filter_rank(raw, rank_q, total_limit)
        meta = {
            "source": source,
            "new_count": new_count,
            "sources_done": [s["name"] for s in streams if s["done"]],
            "sources_pending": [s["name"] for s in streams if not s["done"]],
        }
        try:
            progress_cb(ranked, meta)
        except TypeError:
            progress_cb(ranked)

    while (
        any(not st["done"] for st in streams)
        and len(raw) < total_limit * 2
        and rounds < max_rounds
    ):
        rounds += 1
        active = [st for st in streams if not st["done"]]
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(active)))
        futures = {ex.submit(_fetch_with_timeout, st["fetch"], st["cursor"]): st for st in active}
        try:
            for fut in concurrent.futures.as_completed(
                futures, timeout=_SOURCE_TIMEOUT_SEC + 10
            ):
                st = futures[fut]
                try:
                    page = fut.result() or []
                except Exception:
                    page = []
                st["cursor"] += page_size
                new = [e for e in page if (e.identifier or e.title) not in seen_ids]
                for e in new:
                    seen_ids.add(e.identifier or e.title)
                raw.extend(new)
                if not st["paged"] or not new:
                    st["done"] = True
                _notify(source=st["name"], new_count=len(new))
        except TimeoutError:
            logger.warning("search round timed out; marking slow sources done")
            for fut, st in futures.items():
                if not fut.done():
                    fut.cancel()
                    st["done"] = True
                    _notify(source=st["name"], new_count=0)
        ex.shutdown(wait=False)

    final = _merge_filter_rank(raw, rank_q, total_limit)
    if progress_cb is not None:
        try:
            progress_cb(
                final,
                {
                    "source": None,
                    "new_count": 0,
                    "sources_done": [s["name"] for s in streams],
                    "sources_pending": [],
                    "final": True,
                },
            )
        except TypeError:
            progress_cb(final)
    return final


def search_by_types(
    query: str,
    source_types: list[str],
    req: Requirement | None = None,
    limit_per_source: int = 50,
    total_limit: int = 300,
) -> list[Evidence]:
    """多源检索，合并结果（同步、一次性返回——薄封装 :func:`iter_search`）。

    source_types: 任意子集 ["patents", "literature", "internet", "notebooklm"]。
    "local" 由 /api/ingest 处理，不在此检索。
    """
    return iter_search(
        query,
        source_types,
        req=req,
        total_limit=total_limit,
        per_source_cap=limit_per_source,
    )


def search_chemcrow_web(query: str, limit: int = 5) -> list[Evidence]:
    """Chemistry-optimized web search via ChemCrow's WebSearch tool (SerpAPI).

    ChemCrow's WebSearch is distinct from DuckDuckGo: it uses SerpAPI under
    the hood, which requires a ``SERPAPI_API_KEY`` env var and returns
    chemistry-focused snippets ranked by relevance to scientific queries.
    Falls back to [] when chemcrow is not installed or SerpAPI key absent.
    """
    try:
        from chemcrow.tools import WebSearch  # type: ignore

        tool = WebSearch()
        result_text = tool._run(query) if hasattr(tool, "_run") else tool.run(query)  # type: ignore
        if not result_text:
            return []
        return [
            Evidence(
                source="ChemCrow-Web",
                identifier=f"chemweb:{abs(hash(query)) % 0xFFFF:04x}",
                title=f"SerpAPI: {query[:80]}",
                snippet=str(result_text)[:600],
                relevance=0.88,
            )
        ]
    except Exception as exc:
        logger.warning("ChemCrow WebSearch failed: %s", exc)
        return []


def search_chemcrow_lit(query: str, limit: int = 5) -> list[Evidence]:
    """Chemical literature search via ChemCrow's LiteratureSearch tool.

    ChemCrow's LitSearch queries paper-qa + FAISS over scientific literature,
    returning cited answers from PubMed / arXiv / Semantic Scholar.  It is
    semantically richer than raw arXiv/S2 abstract retrieval because it uses
    paper-qa's embedding-based retrieval and citation extraction.
    Falls back to [] when chemcrow / paper-qa is not installed.
    """
    try:
        from chemcrow.tools import LiteratureSearch  # type: ignore

        tool = LiteratureSearch()
        result_text = tool._run(query) if hasattr(tool, "_run") else tool.run(query)  # type: ignore
        if not result_text:
            return []
        return [
            Evidence(
                source="ChemCrow-Lit",
                identifier=f"chemlit:{abs(hash(query)) % 0xFFFF:04x}",
                title=f"LitSearch: {query[:80]}",
                snippet=str(result_text)[:600],
                relevance=0.92,
            )
        ]
    except Exception as exc:
        logger.warning("ChemCrow LiteratureSearch failed: %s", exc)
        return []


def get_source_availability() -> dict[str, dict]:
    """Check runtime availability of each source type via local import probing.

    Does not make any network requests. Called by /api/search and /api/search/status
    to surface install/config hints in the UI.
    """
    def _ok(*pkgs: str) -> bool:
        for pkg in pkgs:
            try:
                __import__(pkg)
                return True
            except Exception:
                pass
        return False

    from .notebooklm import get_setup_status
    from ..config import get_settings

    s = get_settings()
    serpapi_ok = bool(s.serpapi_api_key)
    tavily_ok = bool(s.tavily_api_key)
    epo_ok = bool(s.epo_consumer_key and s.epo_consumer_secret)
    openalex_ok = bool(s.openalex_enabled and s.openalex_mailto)

    patents_online = _ok("patent_client")
    lit_ok = (
        _ok("arxiv")
        or _ok("semanticscholar")
        or openalex_ok
        or serpapi_ok
    )
    web_ok = _ok("ddgs") or _ok("duckduckgo_search")
    chemcrow_ok = _ok("chemcrow")

    return {
        "patents": {
            "available": True,
            "offline_fallback": True,
            "reason": None if (patents_online or epo_ok) else "offline_seed",
            "hint": (
                None
                if patents_online or epo_ok
                else "配置 EPO OPS 凭证或 pip install -e '.[intel]' 启用 USPTO 专利检索"
            ),
        },
        "literature": {
            "available": lit_ok,
            "offline_fallback": False,
            "reason": None if lit_ok else "library_missing",
            "hint": (
                (None if chemcrow_ok else "pip install -e '.[intel]' 启用 ChemCrow LitSearch")
                if lit_ok
                else "pip install -e '.[intel]' 或配置 OpenAlex mailto / SerpAPI 启用学术检索"
            ),
        },
        "openalex": {
            "available": openalex_ok,
            "offline_fallback": False,
            "reason": None if openalex_ok else "mailto_missing",
            "hint": None if openalex_ok else "FORMUMIND_OPENALEX_MAILTO 未配置",
        },
        "internet": {
            "available": web_ok or serpapi_ok or tavily_ok,
            "offline_fallback": False,
            "reason": None if (web_ok or serpapi_ok or tavily_ok) else "library_missing",
            "hint": (
                "Tavily 已配置，优先于 DuckDuckGo"
                if tavily_ok
                else (
                    None
                    if serpapi_ok
                    else "在设置 → API 配置 中填入 Tavily / SerpAPI 密钥，或 pip install ddgs"
                )
            ),
        },
        "serpapi": {
            "available": serpapi_ok,
            "offline_fallback": False,
            "reason": None if serpapi_ok else "key_missing",
            "hint": None if serpapi_ok else "FORMUMIND_SERPAPI_API_KEY 未配置",
        },
        "tavily": {
            "available": tavily_ok,
            "offline_fallback": False,
            "reason": None if tavily_ok else "key_missing",
            "hint": None if tavily_ok else "FORMUMIND_TAVILY_API_KEY 未配置",
        },
        "epo": {
            "available": epo_ok,
            "offline_fallback": False,
            "reason": None if epo_ok else "key_missing",
            "hint": None if epo_ok else "FORMUMIND_EPO_CONSUMER_KEY/SECRET 未配置",
        },
        "google_patents_cn": {
            "available": serpapi_ok,
            "offline_fallback": False,
            "reason": None if serpapi_ok else "key_missing",
            "hint": None if serpapi_ok else "中文专利需 SerpAPI + chinese_q",
        },
        "cnipa": {
            "available": tavily_ok or serpapi_ok,
            "offline_fallback": False,
            "reason": None if (tavily_ok or serpapi_ok) else "key_missing",
            "hint": None if (tavily_ok or serpapi_ok) else "CNIPA 并行路需 Tavily 或 SerpAPI",
        },
        "chemcrow": {
            "available": chemcrow_ok,
            "offline_fallback": False,
            "reason": None if chemcrow_ok else "library_missing",
            "hint": (
                None
                if chemcrow_ok
                else "pip install -e '.[intel]' 启用 ChemCrow WebSearch (SerpAPI) + LitSearch (paper-qa) 化学增强检索"
            ),
        },
        "notebooklm": get_setup_status(),
        "local": {
            "available": True,
            "offline_fallback": False,
            "reason": None,
            "hint": None,
        },
    }
