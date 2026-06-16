"""Patent & literature intelligence service.

When ``patent_client`` / ``paper-qa`` are installed and configured, this module
fetches real patents from USPTO/EPO. Otherwise it serves a curated offline seed
corpus of representative patent/literature abstracts for the three product
domains, so research always returns cited evidence.
"""
from __future__ import annotations

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
                relevance=max(0.1, 1.0 - i * 0.05),
            ))
        return evidence or None
    except Exception:  # pragma: no cover - network/credentials
        return None


def search(req: Requirement, limit: int = 8) -> list[Evidence]:
    """Backward-compatible public entry point — delegates to search_patents."""
    return search_patents(req, limit)


def search_patents(req: Requirement, limit: int = 5) -> list[Evidence]:
    """专利搜索（patent_client + 种子语料回退）。"""
    result = _online_search(req, limit)
    if result:
        return result
    corpus = SEED_CORPUS.get(req.domain, [])
    evidence = [
        Evidence(relevance=round(max(0.4, 1.0 - i * 0.08), 2), **doc)
        for i, doc in enumerate(corpus)
    ]
    return evidence[:limit]


def search_arxiv(query: str, limit: int = 5) -> list[Evidence]:
    """arXiv 学术预印本搜索（arxiv 库）。"""
    try:
        import arxiv  # type: ignore
        client = arxiv.Client()
        results = list(client.results(arxiv.Search(query=query, max_results=limit, sort_by=arxiv.SortCriterion.Relevance)))
        return [
            Evidence(
                source="arXiv",
                identifier=r.entry_id,
                title=r.title,
                snippet=(r.summary or "")[:500],
                relevance=1.0 - i * 0.1,
            )
            for i, r in enumerate(results)
        ]
    except Exception:
        return []


def search_semantic_scholar(query: str, limit: int = 5) -> list[Evidence]:
    """Semantic Scholar 学术文献搜索。"""
    try:
        from semanticscholar import SemanticScholar  # type: ignore
        sch = SemanticScholar()
        results = sch.search_paper(query, limit=limit)
        out = []
        for i, p in enumerate(results):
            out.append(Evidence(
                source="Semantic Scholar",
                identifier=p.externalIds.get("DOI", p.paperId) if p.externalIds else p.paperId,
                title=p.title or "Untitled",
                snippet=(p.abstract or "")[:500],
                relevance=1.0 - i * 0.1,
            ))
        return out
    except Exception:
        return []


def search_web(query: str, limit: int = 5) -> list[Evidence]:
    """DuckDuckGo 互联网搜索（无需 API key）。"""
    try:
        from duckduckgo_search import DDGS  # type: ignore
        results = list(DDGS().text(query, max_results=limit))
        return [
            Evidence(
                source="Internet",
                identifier=r.get("href", ""),
                title=r.get("title", ""),
                snippet=r.get("body", "")[:500],
                relevance=1.0 - i * 0.1,
            )
            for i, r in enumerate(results)
        ]
    except Exception:
        return []


def search_by_types(
    query: str,
    source_types: list[str],
    req: Requirement | None = None,
    limit_per_source: int = 5,
) -> list[Evidence]:
    """多源并行检索，合并结果。

    source_types: 任意子集 ["patents", "literature", "internet", "local"]
    """
    results: list[Evidence] = []
    q = query or (req.headline() if req else "coating formulation")

    if "patents" in source_types:
        if req:
            results.extend(search_patents(req, limit_per_source))
        else:
            results.extend(search_arxiv(q, limit_per_source))  # fallback

    if "literature" in source_types:
        results.extend(search_arxiv(q, limit_per_source))
        results.extend(search_semantic_scholar(q, limit_per_source))

    if "internet" in source_types:
        results.extend(search_web(q, limit_per_source))

    # "local" 由 ingest 端点单独处理，此处不检索
    # 去重（按 identifier）并按相关度排序
    seen: set[str] = set()
    deduped: list[Evidence] = []
    for e in sorted(results, key=lambda x: x.relevance, reverse=True):
        key = e.identifier or e.title
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    return deduped
