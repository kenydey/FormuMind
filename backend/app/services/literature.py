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
import time
from typing import TYPE_CHECKING
from ..domain.research_query import build_research_query
from ..domain.schemas import Evidence, ProductDomain, Requirement
from ..services.runtime_secrets import effective_setting
from .errors import degrade_return, optional_import

if TYPE_CHECKING:
    from .content_filter import FilterReport

logger = logging.getLogger(__name__)

# Per-source network ceiling — prevents one hung API from blocking the whole search.
_SOURCE_TIMEOUT_SEC = 25

# Shared executor for per-source fetch timeouts (avoid creating a pool per call).
_FETCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)


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
    ProductDomain.autodeposition_coating: [
        # Structured from data/knowledge/autodeposition_emulsion_suppliers.md
        # (Henkel BONDERITE M-PP series + patent WO2017117169A1).
        {"identifier": "BONDERITE-M-PP-866R", "source": "Henkel datasheet", "title": "BONDERITE M-PP 866R epoxy autodeposition bath",
         "snippet": "Black smooth epoxy emulsion film deposited without applied current; working bath pH 2-4; uniform deposition on steel interiors and complex cavities; general corrosion protection."},
        {"identifier": "BONDERITE-M-PP-930C", "source": "Henkel datasheet", "title": "BONDERITE M-PP 930C low-VOC autodeposition coating",
         "snippet": "Epoxy emulsion autodeposition bath compliant with RoHS/REACH, VOC below 0.03 lbs/gal; low-temperature cure around 130 C; for environmentally sensitive applications."},
        {"identifier": "WO2017117169A1", "source": "WIPO", "title": "Low-bake epoxy-acrylate graft autodeposition coating",
         "snippet": "Epoxy-acrylate graft emulsion (Epon-828-type epoxy + MMA/BA acrylics, persulfate initiation, sulfonate/phosphate stabilizer 0.5-5%) with blocked isocyanate crosslinker; stable at pH 1.5-6.0, particle size 0.1-5 um, solids 20-35%; cures below 130 C."},
        {"identifier": "DOI:10.1016/j.porgcoat.2018.03.010", "source": "literature", "title": "Autodeposition mechanism: acid-induced coagulation",
         "snippet": "Autodeposition proceeds by acid-catalyzed coagulation of polymer dispersion at the metal/liquid interface: dissolved Fe2+/Fe3+ (from fluoride etch and oxidizer) destabilizes the latex, and film growth self-limits as deposited polymer blocks further iron dissolution."},
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


def _prepare_search_queries(query: str, domain=None):
    """Return SearchQueries bundle for multi-source search."""
    from .deep_research.query_expander import prepare_search_queries

    return prepare_search_queries(query, domain=domain)


def _seed_evidence(doc: dict, index: int) -> Evidence:
    return Evidence(
        relevance=round(max(0.4, 1.0 - index * 0.08), 2),
        is_seed_corpus=True,
        **doc,
    )


def _fetch_with_timeout(fetch, cursor: int) -> list[Evidence]:
    """Run one source page fetch with a hard timeout."""
    fut = _FETCH_EXECUTOR.submit(fetch, cursor)
    try:
        return fut.result(timeout=_SOURCE_TIMEOUT_SEC) or []
    except Exception as exc:
        return degrade_return(logger, exc, "source fetch timed out or failed", [])


def _build_patent_query(req: Requirement | None, query: str) -> str:
    """Combine user search box text with requirement headline for patent retrieval."""
    parts = [p for p in [query.strip(), build_research_query("", req) if req else ""] if p]
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
    except ImportError:
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
        return degrade_return(logger, exc, "patent_client online search failed", None)


def _search_epo_patents(
    query: str,
    ipc_codes: tuple[str, ...] | list[str] | None,
    limit: int,
    offset: int = 0,
) -> list[Evidence]:
    """EPO Inpadoc search with optional CPC class filter."""
    from ..config import get_settings
    from ..services.patent_client_env import epo_ops_env

    settings = get_settings()
    epo_key = effective_setting(settings, "epo_consumer_key")
    epo_secret = effective_setting(settings, "epo_consumer_secret")
    if not epo_key or not epo_secret:
        return []
    try:
        from patent_client import Inpadoc  # type: ignore

        with epo_ops_env(epo_key, epo_secret):
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
        return degrade_return(logger, exc, "EPO Inpadoc search failed", [])


def search(req: Requirement, limit: int = 8, query: str = "") -> list[Evidence]:
    """Backward-compatible public entry point — delegates to search_patents."""
    return search_patents(req, query=query, limit=limit)


def search_patents(
    req: Requirement | None,
    limit: int = 5,
    offset: int = 0,
    query: str = "",
    *,
    ipc_codes: tuple[str, ...] | list[str] | None = None,
    chinese_query: str = "",
) -> list[Evidence]:
    """专利搜索（EPO + USPTO + Google Patents + 中文专利并行，种子语料回退）。

    Supports a legacy calling convention ``search_patents(offset, query)``
    where the first positional arg is an int offset — used by ``_build_streams``
    when ``req`` is None so that tests mocking ``search_patents`` with a simple
    ``(offset, query)`` signature still intercept the call.
    """
    # Legacy calling convention: search_patents(offset, query)
    if isinstance(req, int):
        offset = req
        if isinstance(limit, str):
            query = limit
            limit = 50
        req = None
    if req is None:
        return search_patents_by_query(
            query, limit=limit, offset=offset, ipc_codes=ipc_codes, chinese_query=chinese_query
        )
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
    if effective_setting(settings, "serpapi_api_key"):
        batches.append(search_serpapi_patents(patent_q, want, 0, settings=settings, domain=getattr(req, "domain", None)))
    cq = (chinese_query or "").strip()
    if cq:
        batches.append(search_google_patents_cn(cq, want, 0, settings=settings))
        batches.append(search_cnipa_parallel(cq, want, 0, settings=settings))
    merged = merge_patent_evidence(*batches, limit=want)
    if merged:
        return merged[offset : offset + limit]
    corpus = SEED_CORPUS.get(req.domain, [])
    seed_query = query or _build_patent_query(req, "")
    evidence = [_seed_evidence(doc, i) for i, doc in enumerate(corpus)]
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
    if effective_setting(settings, "serpapi_api_key"):
        batches.append(search_serpapi_patents(query, want, 0, settings=settings, domain=domain))
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
                all_seeds.append(_seed_evidence(doc, i))
    filtered = _filter_seed_by_query(all_seeds, query)
    return filtered[offset : offset + limit]


_ALLOWED_S2_FIELDS = frozenset({
    "chemistry",
    "materials science",
    "engineering",
    "environmental science",
    "medicine",
})


def search_semantic_scholar(query: str, limit: int = 5, offset: int = 0, *, domain=None) -> list[Evidence]:
    """Semantic Scholar 学术文献搜索（HTTP API + 超时，避免 SDK 挂死）。"""
    try:
        from ..domain.search_profiles import resolve_profile
        _prof = resolve_profile(domain)
        _allowed = (
            frozenset(x.lower() for x in _prof.s2_fields_of_study)
            if _prof is not None
            else _ALLOWED_S2_FIELDS
        )
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
            if fos and not (fos & _allowed):
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
        return degrade_return(logger, exc, "Semantic Scholar search failed", [])


def search_openalex(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    domain=None,
    source_id: str | None = None,
    evidence_source: str = "OpenAlex",
    preferred_source_ids: tuple[str, ...] | list[str] | None = None,
    taxonomy_source: str = "openalex",
) -> list[Evidence]:
    """OpenAlex 学术文献（需 mailto 礼貌池，可在 config 关闭）。"""
    from .search_providers import search_openalex as _openalex

    return _openalex(
        query,
        limit,
        offset,
        domain=domain,
        source_id=source_id,
        evidence_source=evidence_source,
        preferred_source_ids=preferred_source_ids,
        taxonomy_source=taxonomy_source,
    )


def search_serpapi_literature(query: str, limit: int = 5, offset: int = 0) -> list[Evidence]:
    """SerpAPI Scholar → Google Patents 优先链（文献向）。"""
    from .search_providers import search_serpapi_chain

    return search_serpapi_chain(query, limit, offset, prefer="scholar")


def search_internet(query: str, limit: int = 5, offset: int = 0) -> list[Evidence]:
    """互联网检索：Tavily → SerpAPI → DuckDuckGo。

    每一档都把**自己的名字**写进 ``Evidence.source``（`Tavily` / `SerpAPI` /
    `DuckDuckGo`），因为"某档结果质量差"这种判断，只有在能看出是哪一档给的
    结果时才可验证。此前 DuckDuckGo 那档统一标成 `Internet`，于是它的结果和别的
    来源混在一起、无法归因。

    DuckDuckGo 是**唯一不需要 API key** 的一档，所以保留为兜底而不是删掉——
    没有任何密钥也能用是这个项目的设计属性（整个测试套件依赖它）。
    嫌它质量差就把 ``FORMUMIND_WEB_SEARCH_ALLOW_DDGS`` 关掉，此时没有可用密钥
    就诚实地返回空，而不是塞一堆低质量结果冒充检索成功。
    """
    from ..config import get_settings
    from .search_providers import search_serpapi_web, search_tavily

    settings = get_settings()

    if effective_setting(settings, "tavily_api_key"):
        hits = search_tavily(query, limit, offset, settings=settings)
        if hits:
            return hits
        logger.info("internet search: Tavily returned nothing, trying next provider")

    if effective_setting(settings, "serpapi_api_key"):
        hits = search_serpapi_web(query, limit, offset, settings=settings)
        if hits:
            return hits
        logger.info("internet search: SerpAPI returned nothing, trying next provider")

    if not getattr(settings, "web_search_allow_ddgs", True):
        logger.info(
            "internet search: no keyed provider produced results and the "
            "DuckDuckGo fallback is disabled — returning nothing"
        )
        return []
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
                # Named, not the generic "Internet": a result you cannot
                # attribute to a provider is a result whose quality you cannot
                # argue about. Both `_is_weblike` checks match on substrings,
                # so web-specific filtering still applies.
                source="DuckDuckGo",
                identifier=r.get("href") or r.get("url") or "",  # ddgs 新版可能用 url
                title=r.get("title", ""),
                snippet=(r.get("body") or "")[:500],
                relevance=round(max(0.1, 1.0 - (offset + i) * 0.02), 3),
            )
            for i, r in enumerate(results)
        ]
    except Exception as exc:
        return degrade_return(logger, exc, "DuckDuckGo search failed", [])


def search_surechembl_content(
    query: str,
    limit: int = 20,
    offset: int = 0,
    *,
    attach_chemistry: bool = True,
    chemistry_docs: int = 2,
    chemistry_limit: int = 6,
) -> list[Evidence]:
    """SureChEMBL keyword content search → Evidence (source=surechembl).

    Complements EPO/Google patents: chemistry-annotated patent corpus.
    Failures degrade to []. Does not replace ``search_patents``.
    """
    q = (query or "").strip()
    if not q:
        return []
    try:
        from . import surechembl_client as sch

        if not sch.surechembl_enabled():
            return []
        docs = sch.content_search(q, limit=limit, offset=offset)
    except Exception as exc:
        return degrade_return(logger, exc, "SureChEMBL content search failed", [])

    out: list[Evidence] = []
    for i, doc in enumerate(docs):
        doc_id = str(doc.get("doc_id") or "").strip()
        if not doc_id:
            continue
        bits = [
            f"doc {doc_id}",
            f"assignee {doc['assignee']}" if doc.get("assignee") else None,
            f"published {doc['pub_date']}" if doc.get("pub_date") else None,
        ]
        snippet = " · ".join(b for b in bits if b)
        # Optional: annotate top documents with extracted chemistry names.
        if attach_chemistry and offset == 0 and i < max(0, int(chemistry_docs)):
            try:
                from . import surechembl_client as sch

                chems = sch.document_chemistry(doc_id, limit=chemistry_limit)
                names = [c.get("name") for c in chems if c.get("name")]
                if names:
                    snippet = f"{snippet} · chemistry: {', '.join(names[:chemistry_limit])}"
            except Exception:
                pass
        out.append(
            Evidence(
                source="surechembl",
                identifier=doc_id,
                title=str(doc.get("title") or doc_id),
                snippet=snippet[:600],
                relevance=round(max(0.15, 0.92 - (offset + i) * 0.015), 3),
                url=doc.get("url"),
                url_alt=doc.get("surechembl_url"),
                assignee=doc.get("assignee"),
                pub_date=doc.get("pub_date"),
            )
        )
    return out


def _is_patent_or_literature(e: Evidence) -> bool:
    s = (e.source or "").lower()
    if _is_weblike(e):
        return False
    return any(
        k in s
        for k in (
            "uspto", "epo", "patent", "arxiv", "semantic", "literature",
            "chemcrow", "openalex", "doi", "serpapi", "tavily", "cnipa", "google patents",
            "surechembl",
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


def _rank_score_with_boost(e: Evidence, q_kw: set[str], qctx: dict) -> tuple[float, float]:
    base0, base1 = _rank_score(e, q_kw)
    from .search_scoring import evidence_entity_boost, evidence_authority_bonus, domain_match_bonus

    return (
        base0
        + evidence_entity_boost(e, qctx)
        + evidence_authority_bonus(e)
        + domain_match_bonus(e),
        base1,
    )


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
    results: list[Evidence],
    query: str,
    total_limit: int,
    *,
    domain=None,
    req: Requirement | None = None,
) -> tuple[list[Evidence], "FilterReport"]:
    """Filter junk, dedupe, rank by relevance, and cap to ``total_limit``.

    Relevance/junk rules:
    * Offline seed corpus → kept via :func:`_filter_seed_by_query` (query-matched,
      else a couple of top entries so research is never empty).
    * Internet/web hits with zero query-keyword overlap are dropped as junk.
    * Patents / literature require at least one keyword overlap (unless empty query).
    * Domain profile: apply lexical match; ``domain_match=none`` dropped from main list.
    * Literature with explicit ``is_oa=False`` dropped (no fulltext path).
    """
    q_kw = _keywords(query)
    from .search_scoring import query_chem_context

    qctx = query_chem_context(query)
    seeds = [e for e in results if e.identifier in _SEED_IDENTIFIERS]
    online = [e for e in results if e.identifier not in _SEED_IDENTIFIERS]

    # Stamp domain_match for untagged rows before keep/drop decisions.
    if domain is not None:
        from .domain_tagging import apply_domain_match

        for e in online:
            if e.domain_match is None:
                apply_domain_match(e, domain)

    from .domain_tagging import lexical_hits
    from ..domain.search_profiles import resolve_profile
    from ..domain.research_query import wrong_substrate_hit

    profile = resolve_profile(domain)

    def _keep(e: Evidence) -> bool:
        text = f"{e.title} {e.snippet} {e.identifier}"
        if q_kw:
            ov = _overlap(e, q_kw)
            if _is_weblike(e):
                if ov <= 0:
                    return False
            elif _is_patent_or_literature(e):
                if ov < 1:
                    return False
            elif ov <= 0:
                return False
        # Literature without OA path: do not enter the main ingestible list.
        src_l = (e.source or "").lower()
        if e.is_oa is False and (
            "openalex" in src_l
            or "chemrxiv" in src_l
            or "arxiv" in src_l
            or "scholar" in src_l
            or "semantic" in src_l
        ):
            return False
        if e.domain_match == "none":
            return False
        if profile is not None:
            _allow, deny = lexical_hits(text, profile)
            if deny and _allow < 2:
                return False
        if req is not None and wrong_substrate_hit(text, req, query=query):
            return False
        return True

    filtered_online = [e for e in online if _keep(e)]
    merged = filtered_online + _filter_seed_by_query(seeds, query)

    seen: set[str] = set()
    deduped: list[Evidence] = []
    for e in sorted(merged, key=lambda x: _rank_score_with_boost(x, q_kw, qctx), reverse=True):
        key = e.identifier or e.title
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    # Quality rule tier (blocked domains, garbage snippets, SimHash near-dups).
    # Rank order is preserved so the higher-ranked copy of a near-dup survives.
    from .content_filter import filter_evidence

    deduped, _report = filter_evidence(deduped, query)
    by_source: dict[str, int] = {}
    for e in deduped:
        by_source[e.source or "unknown"] = by_source.get(e.source or "unknown", 0) + 1
    _report.source_counts = by_source
    return deduped[:total_limit], _report


def _build_streams(
    patent_query: str,
    western_query: str,
    source_types: list[str],
    req: Requirement | None,
    page_size: int,
    *,
    ipc_codes: tuple[str, ...] | list[str] = (),
    chinese_query: str = "",
    notebooklm_notebook_id: str | None = None,
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
                lambda off, q=patent_query: search_patents(off, q),
                True,
            )
    if "literature" in source_types:
        from ..config import get_settings
        from ..domain.search_profiles import (
            CHEMRXIV_OPENALEX_SOURCE_ID,
            policy_page_size,
            resolve_profile,
        )

        lit_settings = get_settings()
        domain = getattr(req, "domain", None) if req is not None else None
        prof = resolve_profile(domain)

        # Google Scholar (support by default) — lower page size when support.
        scholar_n = policy_page_size(prof, "google_scholar", page_size, default="support")
        if scholar_n > 0:
            add(
                "serpapi_lit",
                lambda off, q=western_query, n=scholar_n: search_serpapi_literature(
                    q, n, offset=off
                ),
                True,
            )

        openalex_n = policy_page_size(prof, "openalex", page_size, default="primary")
        if lit_settings.openalex_enabled and openalex_n > 0:
            prefs = tuple(prof.preferred_openalex_source_ids) if prof is not None else ()
            add(
                "openalex",
                lambda off, q=western_query, d=domain, n=openalex_n, p=prefs: search_openalex(
                    q,
                    n,
                    offset=off,
                    domain=d,
                    preferred_source_ids=p or None,
                ),
                True,
            )

        chemrxiv_n = policy_page_size(prof, "chemrxiv", page_size, default="primary")
        if lit_settings.openalex_enabled and chemrxiv_n > 0:
            crx_id = (
                (prof.chemrxiv_openalex_source_id if prof is not None else "")
                or CHEMRXIV_OPENALEX_SOURCE_ID
            )
            add(
                "chemrxiv",
                lambda off, q=western_query, d=domain, n=chemrxiv_n, sid=crx_id: search_openalex(
                    q,
                    n,
                    offset=off,
                    domain=d,
                    source_id=sid,
                    evidence_source="ChemRxiv",
                    taxonomy_source="chemrxiv",
                ),
                True,
            )

        s2_n = policy_page_size(prof, "semantic_scholar", page_size, default="support")
        if s2_n > 0:
            add(
                "s2",
                lambda off, q=western_query, d=domain, n=s2_n: search_semantic_scholar(
                    q, n, offset=off, domain=d
                ),
                True,
            )

        if s2_n > 0:
            add("chemlit", lambda off, q=western_query: search_chem_lit(q, page_size), False)
    if "internet" in source_types:
        web_q = chinese_query or western_query
        add("internet", lambda off, q=web_q: search_internet(q, page_size, offset=off), True)
        add("chemweb", lambda off, q=western_query: search_chem_web(q, page_size), False)
    if "surechembl" in source_types:
        # Chemistry-annotated patent content; independent of EPO/Google "patents".
        # Keep attach_chemistry off in the timed stream fetch (document-chemistry
        # ZIP is optional enrichment; calling it here can exhaust the shared
        # source executor under LLM retries). Client still exposes the export.
        add(
            "surechembl",
            lambda off, q=patent_query: search_surechembl_content(
                q, page_size, offset=off, attach_chemistry=False
            ),
            True,
        )
    if "notebooklm" in source_types:
        def _nb(off: int, q=western_query, nid=notebooklm_notebook_id) -> list[Evidence]:
            from .notebooklm import search_notebooklm  # 延迟导入：未装库时零开销

            return search_notebooklm(q, page_size, notebook_id=nid)

        add("notebooklm", _nb, False)
    return streams


def iter_search(
    query: str,
    source_types: list[str],
    req: Requirement | None = None,
    total_limit: int = 300,
    per_source_cap: int = 50,
    max_rounds: int = 5,
    progress_cb=None,
    notebooklm_notebook_id: str | None = None,
) -> tuple[list[Evidence], dict]:
    """Incremental multi-source retrieval — fetch in rounds until no source turns
    up new related results (no fixed time budget).

    Each round pulls the next page from every still-active source concurrently.
    ``progress_cb`` (if given) is invoked after **each source** completes (not only
    at round end), so the UI can render results while the search keeps going.
    """
    q = build_research_query(query, req)
    if (query or "").strip():
        sq = _prepare_search_queries(q, domain=getattr(req, "domain", None) if req is not None else None)
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
        notebooklm_notebook_id=notebooklm_notebook_id,
    )

    from ..config import get_settings

    settings = get_settings()
    search_deadline_s = float(getattr(settings, 'search_round_deadline_s', 240) or 240)
    search_deadline = time.monotonic() + search_deadline_s

    raw: list[Evidence] = []
    seen_ids: set[str] = set()
    rounds = 0

    def _notify(*, source: str | None = None, new_count: int = 0) -> None:
        if progress_cb is None:
            return
        ranked, _ = _merge_filter_rank(
            raw, rank_q, total_limit, domain=getattr(req, "domain", None) if req else None, req=req
        )
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
        and rounds < max_rounds
        and time.monotonic() < search_deadline
    ):
        # Early exit: stop paging when we already gathered enough results
        # for ranking (total_limit * 2), even if some sources are still
        # yielding. Each extra round costs _SOURCE_TIMEOUT_SEC per source.
        if len(raw) >= total_limit * 2:
            for st in streams:
                st["done"] = True
            break
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

    final, rule_report = _merge_filter_rank(
        raw, rank_q, total_limit, domain=getattr(req, "domain", None) if req else None, req=req
    )

    # LLM 后处理（quality judge + rerank）可能耗时数百秒（DeepSeek 慢 + 长
    # prompt），期间无 source 完成事件，前端 stall 时钟（300s）会误判任务中止。
    # 发心跳进度重置时钟——这里走 _merge_filter_rank 是幂等的，且非 final 事件
    # 只带 summary，不会污染前端 sources。
    _notify()

    # Optional batched LLM quality judge — one call on the final ranked list.
    from .content_filter import FilterReport, llm_quality_judge

    final, judge_report = llm_quality_judge(final, rank_q)
    _notify()

    filter_report = FilterReport()
    filter_report.merge(rule_report)
    filter_report.merge(judge_report)

    from ..config import get_settings

    settings = get_settings()
    if settings.search_rerank_enabled and len(final) > 1:
        from .rag import llm_rerank

        batch = min(len(final), settings.search_rerank_llm_batch, total_limit)
        head = llm_rerank(rank_q, final[:batch], k=batch, req=req)
        _notify()
        head_keys = {e.identifier or e.title for e in head}
        tail = [
            e
            for e in final[batch:]
            if (e.identifier or e.title) not in head_keys
        ]
        final = (head + tail)[:total_limit]
    else:
        final = final[:total_limit]

    filter_report.kept = len(final)
    filter_payload = filter_report.as_dict()
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
                    "filter_report": filter_payload,
                },
            )
        except TypeError:
            progress_cb(final)
    return final, filter_payload


def search_by_types(
    query: str,
    source_types: list[str],
    req: Requirement | None = None,
    limit_per_source: int = 50,
    total_limit: int = 300,
    notebooklm_notebook_id: str | None = None,
) -> list[Evidence]:
    """多源检索，合并结果（同步、一次性返回——薄封装 :func:`iter_search`）。

    source_types: 任意子集 ["patents", "literature", "internet", "surechembl", "notebooklm"]。
    "local" 由 /api/ingest 处理，不在此检索。
    """
    return iter_search(
        query,
        source_types,
        req=req,
        total_limit=total_limit,
        per_source_cap=limit_per_source,
        notebooklm_notebook_id=notebooklm_notebook_id,
    )[0]


def search_chem_web(query: str, limit: int = 5) -> list[Evidence]:
    """Chemistry-flavoured web search via the SerpAPI scholar-first chain.

    (Formerly wrapped ChemCrow's WebSearch tool, which was itself a SerpAPI
    client; the wrapper was removed 2026-09 in the de-ChemCrow effort.)
    """
    return search_serpapi_literature(query, limit=limit)


_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+")


def split_lit_answer(
    text: str, *, query: str, limit: int = 5, relevance: float = 0.92
) -> list[Evidence]:
    """Split a literature-search answer into per-citation Evidence rows.

    paper-qa answers embed DOIs in their citation lines.  One Evidence per
    unique DOI (identifier ``doi:...``) lets the rows participate in dedup,
    re-ranking and citation chips like any other literature hit.  The full
    answer is always kept as the first row; when no DOI is present the output
    degrades to exactly the legacy single-blob shape.
    """
    body = str(text).strip()
    if not body:
        return []
    out = [
        Evidence(
            source="ChemCrow-Lit",
            identifier=f"chemlit:{abs(hash(query)) % 0xFFFF:04x}",
            title=f"LitSearch: {query[:80]}",
            snippet=body[:600],
            relevance=relevance,
        )
    ]
    seen: set[str] = set()
    for match in _DOI_RE.finditer(body):
        doi = match.group(0).rstrip(".,;)")
        if doi in seen:
            continue
        seen.add(doi)
        # Use the line containing the DOI as the citation title/snippet.
        line_start = body.rfind("\n", 0, match.start()) + 1
        line_end = body.find("\n", match.end())
        line = body[line_start : line_end if line_end >= 0 else len(body)].strip()
        title = line.replace(doi, "").strip(" -–—:.,;()[]") or f"DOI {doi}"
        out.append(
            Evidence(
                source="ChemCrow-Lit",
                identifier=f"doi:{doi}",
                title=title[:160],
                snippet=line[:600] or body[:600],
                relevance=max(0.0, min(1.0, relevance - 0.03)),
            )
        )
        if len(out) >= limit + 1:
            break
    return out


def search_chem_lit(query: str, limit: int = 5) -> list[Evidence]:
    """Chemical literature search via Semantic Scholar (arXiv tier removed).

    (ChemCrow's LiteratureSearch wrapper was removed 2026-09: it required
    paper-qa + an OpenAI key this DeepSeek-only deployment never had, so it
    had always degraded to [] in practice.)
    """
    try:
        return search_semantic_scholar(query, limit=limit)
    except Exception:
        return []


# Compatibility aliases retained for historical imports/tests (de-ChemCrow 2026-09).
search_chemcrow_web = search_chem_web
search_chemcrow_lit = search_chem_lit
split_chemcrow_answer = split_lit_answer


def get_source_availability() -> dict[str, dict]:
    """Check runtime availability of each source type via local import probing.

    Does not make any network requests. Called by /api/search and /api/search/status
    to surface install/config hints in the UI.
    """
    def _ok(*pkgs: str) -> bool:
        return any(optional_import(pkg) for pkg in pkgs)

    def _pip_installed(pkg: str) -> bool:
        try:
            from importlib.metadata import distribution
            distribution(pkg)
            return True
        except Exception:
            return False

    from .notebooklm import get_setup_status
    from ..config import get_settings

    s = get_settings()
    serpapi_ok = bool(effective_setting(s, "serpapi_api_key"))
    tavily_ok = bool(effective_setting(s, "tavily_api_key"))
    epo_ok = bool(
        effective_setting(s, "epo_consumer_key") and effective_setting(s, "epo_consumer_secret")
    )
    openalex_ok = bool(s.openalex_enabled and effective_setting(s, "openalex_mailto"))

    patents_online = _ok("patent_client")
    lit_ok = (
        _ok("semanticscholar")
        or openalex_ok
        or serpapi_ok
    )
    web_ok = _ok("ddgs") or _ok("duckduckgo_search")
    chemcrow_import_ok = _ok("chemcrow")
    chemcrow_installed = chemcrow_import_ok or _pip_installed("chemcrow")

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
                (None if chemcrow_import_ok else "chemcrow 已安装但存在兼容性问题" if chemcrow_installed else "pip install -e '.[intel]' 启用 ChemCrow LitSearch")
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
            "available": chemcrow_installed,
            "offline_fallback": chemcrow_import_ok,
            "reason": None if chemcrow_import_ok else ("compat_issue" if chemcrow_installed else "library_missing"),
            "hint": (
                None
                if chemcrow_import_ok
                else "chemcrow 0.3.7 已安装但与 Pydantic v2 不兼容，WebSearch/LitSearch 将通过其他引擎降级运行"
                if chemcrow_installed
                else "pip install -e '.[intel]' 启用 ChemCrow WebSearch (SerpAPI) + LitSearch (paper-qa) 化学增强检索"
            ),
        },
        "notebooklm": get_setup_status(),
        "surechembl": {
            "available": bool(getattr(s, "surechembl", True)),
            "offline_fallback": False,
            "reason": None if getattr(s, "surechembl", True) else "disabled",
            "hint": (
                None
                if getattr(s, "surechembl", True)
                else "FORMUMIND_SURECHEMBL=false；开启后检索专利化学标注文档"
            ),
        },
        "local": {
            "available": True,
            "offline_fallback": False,
            "reason": None,
            "hint": None,
        },
    }
