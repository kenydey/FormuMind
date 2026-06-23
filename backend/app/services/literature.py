"""Patent & literature intelligence service.

When ``patent_client`` / ``paper-qa`` are installed and configured, this module
fetches real patents from USPTO/EPO. Otherwise it serves a curated offline seed
corpus of representative patent/literature abstracts for the three product
domains, so research always returns cited evidence.
"""
from __future__ import annotations

import concurrent.futures
import re

from ..domain.schemas import Evidence, ProductDomain, Requirement

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


def _online_search(req: Requirement, limit: int) -> list[Evidence] | None:
    """Attempt real patent retrieval; return None if unavailable."""
    try:
        from patent_client import Patent  # type: ignore
    except Exception:
        return None
    try:
        query = req.headline()
        results = Patent.objects.filter(query).limit(limit)  # pragma: no cover - network
        evidence = []
        for i, p in enumerate(results):
            evidence.append(Evidence(
                source="USPTO", identifier=str(getattr(p, "publication_number", f"P{i}")),
                title=str(getattr(p, "title", "")), snippet=str(getattr(p, "abstract", ""))[:400],
                relevance=max(0.1, 1.0 - i * 0.02),
            ))
        return evidence or None
    except Exception:  # pragma: no cover - network/credentials
        return None


def search(req: Requirement, limit: int = 8) -> list[Evidence]:
    """Backward-compatible public entry point — delegates to search_patents."""
    return search_patents(req, limit)


def search_patents(req: Requirement, limit: int = 5, offset: int = 0) -> list[Evidence]:
    """专利搜索（patent_client + 种子语料回退）。``offset`` 支持增量翻页。"""
    result = _online_search(req, limit + offset)
    if result:
        return result[offset : offset + limit]
    corpus = SEED_CORPUS.get(req.domain, [])
    evidence = [
        Evidence(relevance=round(max(0.4, 1.0 - i * 0.08), 2), **doc)
        for i, doc in enumerate(corpus)
    ]
    return evidence[offset : offset + limit]


def search_arxiv(query: str, limit: int = 5, offset: int = 0) -> list[Evidence]:
    """arXiv 学术预印本搜索（arxiv 库）。``offset`` 支持增量翻页。"""
    try:
        import arxiv  # type: ignore
        client = arxiv.Client()
        results = list(client.results(arxiv.Search(
            query=query, max_results=limit + offset, sort_by=arxiv.SortCriterion.Relevance
        )))[offset : offset + limit]
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
    except Exception:
        return []


def search_semantic_scholar(query: str, limit: int = 5, offset: int = 0) -> list[Evidence]:
    """Semantic Scholar 学术文献搜索。``offset`` 支持增量翻页。"""
    try:
        from semanticscholar import SemanticScholar  # type: ignore
        sch = SemanticScholar()
        results = list(sch.search_paper(query, limit=min(100, limit + offset)))[offset : offset + limit]
        out = []
        for i, p in enumerate(results):
            out.append(Evidence(
                source="Semantic Scholar",
                identifier=p.externalIds.get("DOI", p.paperId) if p.externalIds else p.paperId,
                title=p.title or "Untitled",
                snippet=(p.abstract or "")[:500],
                relevance=round(max(0.1, 1.0 - (offset + i) * 0.02), 3),
            ))
        return out
    except Exception:
        return []


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
    except Exception:
        return []


def _overlap(e: Evidence, q_kw: set[str]) -> int:
    """Number of query keywords appearing in an evidence's title+snippet."""
    if not q_kw:
        return 1
    return len(q_kw & _keywords(f"{e.title} {e.snippet}"))


def _is_weblike(e: Evidence) -> bool:
    """Internet/web sources are the junk-prone ones worth filtering hard."""
    s = (e.source or "").lower()
    return "internet" in s or "web" in s or "duck" in s


def _merge_filter_rank(
    results: list[Evidence], query: str, total_limit: int
) -> list[Evidence]:
    """Filter junk, dedupe, rank by relevance, and cap to ``total_limit``.

    Relevance/junk rules:
    * Offline seed corpus → kept via :func:`_filter_seed_by_query` (query-matched,
      else a couple of top entries so research is never empty).
    * Internet/web hits with zero query-keyword overlap are dropped as junk.
    * arXiv / Semantic Scholar / patents are already query-targeted and pass
      through, but everything is ranked by (keyword overlap, source relevance).
    """
    q_kw = _keywords(query)
    seeds = [e for e in results if e.identifier in _SEED_IDENTIFIERS]
    online = [e for e in results if e.identifier not in _SEED_IDENTIFIERS]
    filtered_online = [
        e for e in online if not _is_weblike(e) or _overlap(e, q_kw) > 0
    ]
    merged = filtered_online + _filter_seed_by_query(seeds, query)

    seen: set[str] = set()
    deduped: list[Evidence] = []
    for e in sorted(merged, key=lambda x: (_overlap(x, q_kw), x.relevance), reverse=True):
        key = e.identifier or e.title
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    return deduped[:total_limit]


def _build_streams(
    query: str, source_types: list[str], req: Requirement | None, page_size: int
) -> list[dict]:
    """One paged stream per source. ``paged`` sources support offset/round paging;
    single-shot sources (chemcrow/notebooklm) yield once then finish."""
    streams: list[dict] = []

    def add(name: str, fetch, paged: bool) -> None:
        streams.append({"name": name, "fetch": fetch, "cursor": 0, "paged": paged, "done": False})

    if "patents" in source_types:
        if req is not None:
            add("patents", lambda off: search_patents(req, page_size, offset=off), True)
        else:  # no requirement → use arXiv as a stand-in patent stream
            add("patents", lambda off: search_arxiv(query, page_size, offset=off), True)
    if "literature" in source_types:
        add("arxiv", lambda off: search_arxiv(query, page_size, offset=off), True)
        add("s2", lambda off: search_semantic_scholar(query, page_size, offset=off), True)
        add("chemlit", lambda off: search_chemcrow_lit(query, page_size), False)
    if "internet" in source_types:
        add("web", lambda off: search_web(query, page_size, offset=off), True)
        add("chemweb", lambda off: search_chemcrow_web(query, page_size), False)
    if "notebooklm" in source_types:
        def _nb(off: int) -> list[Evidence]:
            from .notebooklm import search_notebooklm  # 延迟导入：未装库时零开销

            return search_notebooklm(query, page_size)

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

    Each round pulls the next page from every still-active source concurrently
    (no per-source timeout); a source is marked done when it is single-shot or a
    round yields no new unseen identifiers. ``progress_cb`` (if given) receives the
    ranked-so-far evidence after every round, so the UI can render results while
    the search keeps going. Stops when all sources are exhausted, ~``total_limit``
    related results are gathered, or the ``max_rounds`` safety bound is hit.
    """
    q = query or (req.headline() if req else "coating formulation")
    page_size = max(1, min(per_source_cap, 50))
    streams = _build_streams(q, source_types, req, page_size)

    raw: list[Evidence] = []
    seen_ids: set[str] = set()
    rounds = 0
    while (
        any(not st["done"] for st in streams)
        and len(raw) < total_limit * 2
        and rounds < max_rounds
    ):
        rounds += 1
        active = [st for st in streams if not st["done"]]
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(active)))
        futures = {ex.submit(st["fetch"], st["cursor"]): st for st in active}
        # No timeout: stopping is driven by results, not the clock.
        for fut in concurrent.futures.as_completed(futures):
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
            # Exhausted: single-shot source, or a round with no new sources.
            if not st["paged"] or not new:
                st["done"] = True
        ex.shutdown(wait=False)
        if progress_cb is not None:
            progress_cb(_merge_filter_rank(raw, q, total_limit))

    final = _merge_filter_rank(raw, q, total_limit)
    if progress_cb is not None:
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
    except Exception:
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
    except Exception:
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

    patents_online = _ok("patent_client")
    lit_ok = _ok("arxiv") and _ok("semanticscholar")
    web_ok = _ok("ddgs") or _ok("duckduckgo_search")
    chemcrow_ok = _ok("chemcrow")

    return {
        "patents": {
            "available": True,  # always available via offline seed corpus
            "offline_fallback": not patents_online,
            "reason": None if patents_online else "offline_seed",
            "hint": (
                None
                if patents_online
                else "pip install -e '.[intel]' 启用真实 USPTO/EPO 专利检索"
            ),
        },
        "literature": {
            "available": lit_ok,
            "offline_fallback": False,
            "reason": None if lit_ok else "library_missing",
            "hint": (
                (None if chemcrow_ok else "pip install -e '.[intel]' 启用 ChemCrow LitSearch (SerpAPI + paper-qa) 化学文献检索")
                if lit_ok
                else "pip install -e '.[intel]' 启用 arXiv + Semantic Scholar + ChemCrow 学术文献检索"
            ),
        },
        "internet": {
            "available": web_ok,
            "offline_fallback": False,
            "reason": None if web_ok else "library_missing",
            "hint": (
                (None if chemcrow_ok else "pip install -e '.[intel]' 启用 ChemCrow WebSearch (SerpAPI) 化学优化搜索")
                if web_ok
                else "pip install -e '.[intel]' 启用 DuckDuckGo + ChemCrow 互联网检索"
            ),
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
