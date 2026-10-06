"""Patent & literature intelligence service.

With EPO OPS credentials configured (``services.epo_ops`` - plain httpx against the official REST interface) this
module fetches real patents from the EPO's worldwide DOCDB (US, EP, WO, CN, JP, ...); Google Patents / SerpAPI /
CNIPA add more. Otherwise it serves a curated offline seed corpus of representative patent/literature abstracts
for the three product domains, so research always returns cited evidence.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import re
import threading
import time
from typing import TYPE_CHECKING, Sequence
from ..domain.research_query import build_research_query
from ..domain.schemas import Evidence, Passage, ProductDomain, Requirement
from ..services.runtime_secrets import effective_setting
from .errors import degrade_return, optional_import
from .literature_identity import identity_keys
from .http_safe import make_client

if TYPE_CHECKING:
    from .content_filter import FilterReport

logger = logging.getLogger(__name__)

# W3-3 (P1-24): fulltext_enrich emits one Evidence row per fetched chunk with
# identifier "{original_identifier}#p{N}". The fragment is stripped for store
# lookups so a document enrich already persisted is reused, not re-fetched.
_ENRICH_CHUNK_ID_RE = re.compile(r"^(?P<base>.+)#p(?P<idx>\d+)$")

# Per-source network ceiling — prevents one hung API from blocking the whole search.
_SOURCE_TIMEOUT_SEC = 25

# Shared executor for per-source fetch timeouts (avoid creating a pool per call).
_FETCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)


# Wave 1 · 会话级检索缓存（P0-13）：同 query/source/limit 指纹在 TTL 内直接
# 复用上次结果，避免重复检索烧 token 与耗时。TTL 取配置 search_cache_ttl_s
#（秒，0=关闭，默认 600）；容量 128，超限淘汰最旧条目；threading.Lock 保护。
_SEARCH_CACHE: dict[str, tuple[float, list[Evidence], dict]] = {}
_SEARCH_CACHE_LOCK = threading.Lock()
_SEARCH_CACHE_MAX_SIZE = 128


def _search_cache_key(
    query: str,
    source_types: "list[str] | None",
    total_limit: int,
    per_source_cap: int,
    domain,
    req=None,
) -> str:
    """sha256(query | 排序后 source_types | total_limit | per_source_cap | domain | req指纹)。

    P1-2: req 指纹（substrate 等）必须进 key。_merge_filter_rank 用 req 做
    substrate 过滤，旧 key 缺 req 维度，换需求后 600s 内命中脏缓存。
    """
    req_fp = ""
    if req is not None:
        fp_parts = [
            str(getattr(req, "substrate", "") or ""),
            str(getattr(req, "salt_spray_hours", "") or ""),
            str(getattr(req, "product_type", "") or ""),
            str(getattr(req, "project_id", "") or ""),
            str(getattr(req, "notebooklm_notebook_id", "") or ""),
        ]
        req_fp = hashlib.sha256("|".join(fp_parts).encode("utf-8")).hexdigest()[:16]
    payload = "|".join(
        [
            str(query or ""),
            ",".join(sorted(str(s) for s in (source_types or []))),
            str(total_limit),
            str(per_source_cap),
            str(domain or ""),
            req_fp,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _search_cache_get(key: str, ttl_s: int) -> "tuple[list[Evidence], dict] | None":
    """TTL 内命中返回 (final, payload)；过期/缺失返回 None。"""
    try:
        with _SEARCH_CACHE_LOCK:
            rec = _SEARCH_CACHE.get(key)
            if rec is None:
                return None
            ts, final, payload = rec
            if time.monotonic() - ts > ttl_s:
                _SEARCH_CACHE.pop(key, None)
                return None
            return final, payload
    except Exception:
        return None


def _search_cache_put(key: str, final: list[Evidence], payload: dict) -> None:
    """写入缓存；容量超限时淘汰时间戳最旧的条目。fail-open：异常不炸主流程。"""
    try:
        with _SEARCH_CACHE_LOCK:
            if key not in _SEARCH_CACHE and len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX_SIZE:
                oldest = min(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][0])
                _SEARCH_CACHE.pop(oldest, None)
            _SEARCH_CACHE[key] = (time.monotonic(), final, payload)
    except Exception:
        logger.debug("search cache put failed", exc_info=True)


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
    """Tokenize for overlap scoring.

    Drops pure digits and 1–2 char ASCII fragments so CAS pieces (``7440-66-6`` →
    ``6``) and IPC tail numbers cannot create false seed matches. Chinese stays
    character-level (len 1) because that is how bilingual queries are written.
    """
    out: set[str] = set()
    for tok in _KW_RE.findall(text.lower()):
        if tok.isdigit():
            continue
        if tok.isascii() and len(tok) < 3:
            continue
        out.add(tok)
    return out


def _filter_seed_by_query(
    seeds: list[Evidence], query: str, min_keep: int = 2
) -> list[Evidence]:
    """Keep seed entries whose title+snippet share keywords with the query.

    When the query has been expanded with substrate / IPC / CAS noise, a single
    generic hit (e.g. ``steel``) must not keep every coating seed. Prefer the
    strongest overlaps; fall back to ``min_keep`` top-relevance rows when none
    match, so offline research always returns some cited evidence.
    """
    q_kw = _keywords(query)
    if not q_kw or not seeds:
        return seeds
    scored: list[tuple[int, Evidence]] = []
    for e in seeds:
        n = len(q_kw & _keywords(f"{e.title} {e.snippet}"))
        if n:
            scored.append((n, e))
    if not scored:
        return sorted(seeds, key=lambda x: x.relevance, reverse=True)[:min_keep]
    best = max(n for n, _ in scored)
    threshold = best if best >= 2 else 1
    return [e for n, e in scored if n >= threshold]


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


def _search_epo_patents(
    query: str,
    ipc_codes: tuple[str, ...] | list[str] | None,
    limit: int,
    offset: int = 0,
) -> list[Evidence]:
    """EPO OPS search (US, EP, WO, CN, JP, ...) with an optional CPC class filter; [] without credentials."""
    from .search_providers import search_epo_patents

    return search_epo_patents(query, limit, offset, cpc_codes=list(ipc_codes or []))


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
    """专利搜索（EPO OPS（含 US / EP / WO / CN / JP 等）+ Google Patents + 中文专利并行，种子语料回退）。

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
    if effective_setting(settings, "serpapi_api_key"):
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
        want = min(100, (limit + offset) * 3)
        with make_client(timeout=_SOURCE_TIMEOUT_SEC) as client:
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
    arm: str | None = None,
    arm_delta: float = 0.0,
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
        arm=arm,
        arm_delta=arm_delta,
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
    from .search_scoring import (
        evidence_entity_boost,
        evidence_authority_bonus,
        domain_match_bonus,
        search_deny_penalty,
        age_normalized_citation_score,
    )
    from .citation_date_guard import future_pub_date_penalty

    return (
        base0
        + evidence_entity_boost(e, qctx)
        + evidence_authority_bonus(e)
        + domain_match_bonus(e)
        + search_deny_penalty(e)
        # W4-6 · P0-19: citedBy 按论文年龄归一化（引用速率而非总量）
        + age_normalized_citation_score(e)
        # W4-6 · P0-18: 未来出版日期降权（防幻觉/坏元数据）
        + future_pub_date_penalty(e),
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

    from .domain_tagging import lexical_hits, search_deny_hits
    from ..domain.search_profiles import resolve_profile
    from ..domain.research_query import wrong_substrate_hit
    from ..config import get_settings

    profile = resolve_profile(domain)
    deny_on = bool(getattr(get_settings(), "kb_search_deny_enabled", True))

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
            if deny_on:
                deny = max(deny, search_deny_hits(text, profile))
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


_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.I)


def _evidence_key(e: Evidence) -> str:
    """Stable cross-source key so the same work dedupes across arms."""
    ident = str(e.identifier or "").strip()
    ident = _DOI_PREFIX_RE.sub("", ident).strip().casefold()
    return ident or str(e.title or "").strip().casefold()


def _dedupe_evidence(items: list[Evidence]) -> list[Evidence]:
    """First-wins dedupe, preserving arm order (precise before recall/broad)."""
    seen: set[str] = set()
    out: list[Evidence] = []
    for e in items or ():
        key = _evidence_key(e)
        if not key:
            out.append(e)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _evidence_identity_keys(e: Evidence) -> set[str]:
    """Evidence 的跨轮次身份键：doi/arXiv 归一化 + 标题回退键。

    供 ``iter_search`` 的轮次间去重使用——同一文献在不同 source/轮次以
    不同写法（``DOI:…`` / ``https://doi.org/…`` / 大小写差异）出现时也能
    被识别。Evidence 无 year/author 字段，故不建 tiyr 复合键；无任何身份
    键时回退到 ``raw:<identifier|title>``（等价旧行为）。
    """
    try:
        keys = identity_keys(
            {
                "title": e.title or "",
                "doi": e.identifier or "",
                "arxiv": e.identifier or "",
            }
        )
    except Exception:
        keys = set()
    if not keys:
        keys = {"raw:" + str(e.identifier or e.title or "").strip().casefold()}
    return keys


class _TokenBucket:
    """Thread-safe token bucket: ``rate`` tokens accrue per second.

    Used to keep parallel OpenAlex arms under the 5 req/s broad-boolean
    limit (see ``arm_queries``): each arm acquires one token per page it is
    about to fetch, so the aggregate request rate across arm threads stays
    bounded even though pages inside one arm are sequential.
    """

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self._rate = float(rate)
        self._capacity = float(capacity if capacity is not None else rate)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: float = 1.0) -> None:
        n = float(n)
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._updated) * self._rate,
                )
                self._updated = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                wait_s = (n - self._tokens) / self._rate
            time.sleep(wait_s)


# P-4: shared OpenAlex request budget across parallel arm threads.
_OPENALEX_MAX_REQ_PER_S = 5.0
_openalex_bucket = _TokenBucket(_OPENALEX_MAX_REQ_PER_S)


def openalex_arms(
    terms: "list[str] | tuple[str, ...]",
    limit: int,
    offset: int = 0,
    *,
    domain=None,
    req: Requirement | None = None,
    preferred_source_ids=None,
) -> list[Evidence]:
    """Fetch the OpenAlex arm set for one page and return the merged union.

    A single concatenated query is a lottery: OpenAlex ANDs space-separated words
    and stems them, so the same topic returned **1** hit in one run and full
    pages in another (「铝合金碱性脱脂剂」: 1 hit, 0% domain hit rate). Running the
    arms and merging turns that cliff into a floor — measured on the same three
    topics, 1→37, 25→72, 25→69 unique rows, with domain hit rates 62%/47%/99%
    (baseline 0%/40%/100%) and less competing-substrate noise (12%→6.9%).

    Arm order matters for dedupe: precise rows win ties, so the user's own
    wording keeps its position when the broad arm finds the same work.

    The broad arm is conditional (`openalex_arm_broad_threshold`): it returns
    hundreds of thousands of hits and discards the user's specific wording, so it
    only fires when the other two came back thin, and only on the first page —
    it is a rescue net, not a paging source.
    """
    from ..config import get_settings
    from .search_providers import arm_queries

    settings = get_settings()
    profile = None
    try:
        from ..domain.search_profiles import resolve_profile

        profile = resolve_profile(domain)
    except Exception:
        profile = None

    if not getattr(settings, "openalex_multi_arm", True):
        return search_openalex(
            " ".join(terms or ()), limit, offset,
            domain=domain, preferred_source_ids=preferred_source_ids,
        )

    arms = arm_queries(terms, req=req, profile=profile, settings=settings)
    if not arms:
        return []

    deferred = [a for a in arms if a.conditional]
    primary = [a for a in arms if not a.conditional]

    def _run_arm(arm):
        # Rate-limit at arm granularity: one token per page this arm will
        # fetch (search_openalex pages 25 per request). The broad_boolean
        # 5 req/s budget is shared across the arm threads.
        _openalex_bucket.acquire(max(1, (limit + 24) // 25))
        try:
            rows = search_openalex(
                arm.query, limit, offset,
                domain=domain, preferred_source_ids=preferred_source_ids,
                arm=arm.name, arm_delta=arm.relevance_delta,
            )
        except Exception as exc:  # noqa: BLE001 — one dead arm must not kill the search
            logger.debug("openalex arm %s failed: %s", arm.name, exc)
            rows = []
        return arm, rows or []

    merged: list[Evidence] = []
    counts: list[str] = []
    # P-4: primary arms run in parallel (one thread per arm). Results are
    # collected in arm order so _dedupe_evidence keeps the same winner as the
    # old serial loop — precise rows still win ties.
    if primary:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(primary), 4)
        ) as ex:
            futures = [ex.submit(_run_arm, arm) for arm in primary]
            for fut, arm in zip(futures, primary):
                try:
                    _, rows = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("openalex arm %s crashed: %s", arm.name, exc)
                    rows = []
                counts.append(f"{arm.name}={len(rows)}")
                merged.extend(rows)
    merged = _dedupe_evidence(merged)

    if deferred and offset == 0 and len(merged) < int(settings.openalex_arm_broad_threshold):
        for arm in deferred:
            _openalex_bucket.acquire(max(1, (limit + 24) // 25))
            rows = search_openalex(
                arm.query, limit, offset,
                domain=domain, preferred_source_ids=preferred_source_ids,
                arm=arm.name, arm_delta=arm.relevance_delta,
            )
            counts.append(f"{arm.name}={len(rows)}(rescue)")
            merged.extend(rows)
        merged = _dedupe_evidence(merged)

    # Per-arm observability (M4): the lottery is invisible from the merged stream
    # alone, so the counts go to the log every page.
    logger.info("openalex arms off=%s merged=%s [%s]", offset, len(merged), " ".join(counts))
    return merged


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
    western_terms: "Sequence[str] | None" = None,
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
            # Prefer the structured expansion so arm 1 can truncate on *terms*
            # rather than words — splitting the joined string would break
            # multi-word phrases like "chemical conversion coating" apart.
            cr_terms = list(western_terms or ()) or (western_query or "").split()
            add(
                "openalex",
                lambda off, t=cr_terms, d=domain, n=openalex_n, p=prefs, r=req: openalex_arms(
                    t,
                    n,
                    offset=off,
                    domain=d,
                    req=r,
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
            # ChemRxiv is a single ~63k-work repository, and OpenAlex ANDs the
            # words of the expanded keyword string — so the query that works over
            # the whole corpus returned exactly **1** hit inside it. A grouped
            # boolean (`(substrate…) AND (process…)`) restores recall to ~1,057
            # with the top ranks on topic. See `venue_scoped_query`.
            from .search_providers import venue_scoped_query

            crx_q = venue_scoped_query((western_query or "").split(), req=req, profile=prof)
            add(
                "chemrxiv",
                lambda off, q=crx_q, d=domain, n=chemrxiv_n, sid=crx_id: search_openalex(
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


def _final_progress_meta(filter_payload: dict, sources_done: "list[str] | None" = None) -> dict:
    """iter_search 终态 progress meta 形状（缓存命中与正常结束共用）。"""
    return {
        "source": None,
        "new_count": 0,
        "sources_done": sources_done or [],
        "sources_pending": [],
        "final": True,
        "filter_report": filter_payload,
    }


def _emit_final_progress(progress_cb, final: list[Evidence], meta: dict) -> None:
    """终态 progress 回调：兼容只接受 (partial,) 的旧回调签名。"""
    if progress_cb is None:
        return
    try:
        progress_cb(final, meta)
    except TypeError:
        progress_cb(final)


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
    Interim invocations carry the cumulative *unranked* raw list
    (``meta["incremental"]=True``); the full filter+rank runs once and its
    result is delivered by the terminal (``meta["final"]=True``) callback.

    Wave 1 · 会话缓存（P0-13）：入口先查 ``_SEARCH_CACHE``，TTL 内命中直接
    返回缓存结果并仍触发一次终态 ``progress_cb``，保持前端行为一致。
    """
    from ..config import get_settings

    _cache_settings = get_settings()
    cache_ttl_s = int(getattr(_cache_settings, "search_cache_ttl_s", 600) or 0)
    domain_hint = getattr(req, "domain", None) if req is not None else None
    cache_key = _search_cache_key(query, source_types, total_limit, per_source_cap, domain_hint, req)
    if cache_ttl_s > 0:
        hit = _search_cache_get(cache_key, cache_ttl_s)
        if hit is not None:
            cached_final, cached_payload = hit
            logger.info("search cache hit query=%r", (query or "")[:60])
            _emit_final_progress(
                progress_cb, cached_final, _final_progress_meta(cached_payload)
            )
            return cached_final, cached_payload

    q = build_research_query(query, req)
    western_terms: list[str] = []
    if (query or "").strip():
        sq = _prepare_search_queries(q, domain=getattr(req, "domain", None) if req is not None else None)
        rank_q = sq.rank_q
        patent_q = sq.patent_q
        western_q = sq.western_q
        chinese_q = sq.chinese_q
        ipc_codes = sq.ipc_codes
        # Structured expansion: the arm builder truncates and OR-joins on terms,
        # not words, so phrases survive intact.
        western_terms = list(getattr(sq.expanded, "english_synonyms", None) or [])
    else:
        rank_q = patent_q = western_q = chinese_q = q
        ipc_codes = ()
    page_size = max(1, min(per_source_cap, 50))
    streams = _build_streams(
        patent_q, western_q, source_types, req, page_size,
        ipc_codes=ipc_codes, chinese_query=chinese_q,
        notebooklm_notebook_id=notebooklm_notebook_id,
        western_terms=western_terms,
    )

    from ..config import get_settings

    settings = get_settings()
    # B-15: declared on Settings (app/config.py); was an undeclared getattr
    # fallback stuck at 240s and not configurable.
    search_deadline_s = float(settings.search_round_deadline_s or 240)
    search_deadline = time.monotonic() + search_deadline_s

    raw: list[Evidence] = []
    seen_ids: set[str] = set()
    rounds = 0

    def _notify(*, source: str | None = None, new_count: int = 0) -> None:
        # P-7: interim notifications are incremental — the cumulative raw list
        # with no re-rank. The full _merge_filter_rank runs exactly once at the
        # end of iter_search (the final _emit_final_progress call). Consumers
        # only use len(partial) / new_count for progress display; the ranked
        # final list arrives via the terminal callback.
        if progress_cb is None:
            return
        meta = {
            "source": source,
            "new_count": new_count,
            "sources_done": [s["name"] for s in streams if s["done"]],
            "sources_pending": [s["name"] for s in streams if not s["done"]],
            "incremental": True,
        }
        try:
            progress_cb(list(raw), meta)
        except TypeError:
            progress_cb(list(raw))

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
                # 轮次间去重改用身份键（W1-4）：doi 大小写/前缀写法差异不再重复抓取。
                new: list[Evidence] = []
                for e in page:
                    e_keys = _evidence_identity_keys(e)
                    if e_keys & seen_ids:
                        continue
                    seen_ids.update(e_keys)
                    new.append(e)
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

    # Wave 1 · 检索 MMR 多样性重排（P0-10）：放在 _merge_filter_rank 之后、
    # llm_quality_judge 之前，先做多样性可省 judge token。flag 默认关闭。
    mmr_applied = False
    try:
        if getattr(settings, "search_mmr_enabled", False) and len(final) > 1:
            from .recommend_diversity import select_diverse_mmr_text

            mmr_lambda = float(getattr(settings, "search_mmr_lambda", 0.7) or 0.7)
            reranked = select_diverse_mmr_text(
                final, min(len(final), total_limit), lambda_score=mmr_lambda
            )
            if reranked:
                final = reranked
                mmr_applied = True
                logger.info("search MMR applied n=%d lambda=%.2f", len(final), mmr_lambda)
    except Exception:
        # fail-open：MMR 失败不影响主流程，保持 rule 排序。
        logger.debug("search MMR failed; keeping rule order", exc_info=True)

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
    rerank_meta: dict = {"applied": False, "backend": "none"}
    if settings.search_rerank_enabled and len(final) > 1:
        from .rag import rerank_scored

        batch = min(len(final), settings.search_rerank_llm_batch, total_limit)
        scored, rerank_meta = rerank_scored(
            rank_q, final[:batch], k=batch, req=req
        )
        head = [it.evidence for it in scored]
        if not rerank_meta.get("applied"):
            logger.warning(
                "search rerank not applied (backend=%s reason=%s); keeping rule order",
                rerank_meta.get("backend"),
                rerank_meta.get("reason"),
            )
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
    # W2-2 (P1-7): mark which hits already have full text persisted, so the
    # read stage can skip the download. Fail-open: marking never breaks search.
    try:
        from ..db.source_store import get_source_store
        from .patent_ids import canonical_origin_url

        _store = get_source_store()
        for _e in final:
            try:
                _origin = canonical_origin_url(
                    _e.identifier, url=getattr(_e, "url", None)
                )
                _doc = _store.find_by_origin_url(_origin) if _origin else None
                _e.has_fulltext = bool(_doc and getattr(_doc, "full_text", None))
            except Exception:
                _e.has_fulltext = False
    except Exception:
        logger.debug("has_fulltext marking failed", exc_info=True)
    filter_payload = filter_report.as_dict()
    filter_payload["rerank_applied"] = bool(rerank_meta.get("applied"))
    filter_payload["rerank_backend"] = rerank_meta.get("backend") or "none"
    filter_payload["mmr_applied"] = mmr_applied
    _emit_final_progress(
        progress_cb,
        final,
        _final_progress_meta(filter_payload, [s["name"] for s in streams]),
    )
    # Wave 1 · 会话缓存写入（P0-13）：TTL=0 时关闭写入。
    if cache_ttl_s > 0:
        _search_cache_put(cache_key, final, filter_payload)
    return final, filter_payload


def read_passages(
    evidence: Evidence,
    *,
    max_pages: int = 5,
    max_chars: int = 12000,
    project_id: str | None = None,
) -> tuple[list[Passage], dict]:
    """W2-2 (P1-7): second stage of search→read — fetch full text for one hit.

    Search returns metadata + snippets only; call this on shortlisted hits to
    get page-anchored passages. Never raises — fail-open returns ``([], meta)``
    with ``meta["reason"]`` explaining why.

    W3-3 (P1-24): when ``fulltext_enrich`` already produced the source's chunks
    (enrich emits one Evidence row per chunk as ``{identifier}#p{N}``), the
    persisted document is reused — the fetcher is not called again. The enrich
    stage and the read stage share the ``max_chars`` budget pool: characters
    the enrich stage already delivered (the chunk row's snippet) count against
    the read budget. Returned passages carry ``origin`` = "enrich" | "ondemand".
    """
    meta: dict = {"ok": False, "truncated": False, "source_id": None, "reason": ""}
    try:
        if evidence is None or not getattr(evidence, "identifier", None):
            meta["reason"] = "no_identifier"
            return [], meta
        from ..config import get_settings
        from ..db.source_store import get_source_store
        from .chunking import chunk_markdown
        from .patent_ids import canonical_origin_url

        settings = get_settings()
        store = get_source_store()
        # W3-3 (P1-24): strip the enrich chunk-row fragment for the store lookup
        # so a document enrich already persisted is reused, not re-fetched.
        identifier = evidence.identifier or ""
        _frag = _ENRICH_CHUNK_ID_RE.match(identifier)
        enrich_chunk_row = _frag is not None
        lookup_identifier = _frag.group("base") if _frag else identifier
        origin = canonical_origin_url(
            lookup_identifier, url=getattr(evidence, "url", None)
        )
        doc = store.find_by_origin_url(origin) if origin else None
        fetched_now = False
        if doc is None or not getattr(doc, "full_text", None):
            # On-demand fetch, gated by fulltext_enrich — no silent downloads.
            if not getattr(settings, "fulltext_enrich", False):
                meta["reason"] = "no_fulltext"
                return [], meta
            from .fulltext_fetcher import enrich_search_results

            try:
                # Fetch by the base identifier so the persisted origin_url stays
                # consistent with later (fragment-stripped) lookups.
                fetch_evidence = (
                    evidence.model_copy(update={"identifier": lookup_identifier})
                    if enrich_chunk_row
                    else evidence
                )
                enrich_search_results([fetch_evidence], max_docs=1, project_id=project_id)
            except Exception as fetch_exc:  # noqa: BLE001
                logger.debug("read_passages on-demand fetch failed: %s", fetch_exc)
            # Re-obtain the store: the fetch may have persisted via another session.
            store = get_source_store()
            doc = store.find_by_origin_url(origin) if origin else None
            if doc is None or not getattr(doc, "full_text", None):
                meta["reason"] = "fetch_failed"
                return [], meta
            fetched_now = True
        meta["source_id"] = doc.id
        # W3-3 (P1-24): enrich and read share the max_chars budget pool —
        # characters the enrich stage already delivered (this chunk row's
        # snippet) count against the read truncation budget.
        enrich_consumed = (
            len(getattr(evidence, "snippet", None) or "") if enrich_chunk_row else 0
        )
        budget = max(0, max_chars - enrich_consumed)
        meta["enrich_consumed_chars"] = enrich_consumed
        from_enrich = (not fetched_now) and (
            enrich_chunk_row
            or (getattr(doc, "extraction_status", None) or "") == "fulltext"
        )
        passage_origin = "enrich" if from_enrich else "ondemand"
        chunks = chunk_markdown(
            doc.full_text,
            max_chars=int(getattr(settings, "ingest_chunk_max_chars", 1600) or 1600),
            overlap=int(getattr(settings, "ingest_chunk_overlap", 200) or 200),
        )
        passages: list[Passage] = []
        seen_pages: set = set()
        total = 0
        for c in chunks:
            ctext = c.text or ""
            if len(ctext.strip()) <= 30:
                continue
            if c.page_no is not None:
                if c.page_no not in seen_pages and len(seen_pages) >= max_pages:
                    meta["truncated"] = True
                    break
                seen_pages.add(c.page_no)
            if total + len(ctext) > budget and passages:
                meta["truncated"] = True
                break
            passages.append(
                Passage(
                    source_id=doc.id,
                    page_no=c.page_no,
                    char_start=getattr(c, "offset_start", None),
                    char_end=getattr(c, "offset_end", None),
                    section_title=getattr(c, "heading_path", "") or "",
                    text=ctext,
                    origin=passage_origin,
                )
            )
            total += len(ctext)
        meta["ok"] = True
        return passages, meta
    except Exception as exc:  # noqa: BLE001 — read stage never breaks callers
        logger.debug("read_passages failed: %s", exc, exc_info=True)
        meta["reason"] = "error"
        return [], meta


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

    from .notebooklm import get_setup_status
    from ..config import get_settings

    s = get_settings()
    serpapi_ok = bool(effective_setting(s, "serpapi_api_key"))
    tavily_ok = bool(effective_setting(s, "tavily_api_key"))
    epo_keys = bool(
        effective_setting(s, "epo_consumer_key") and effective_setting(s, "epo_consumer_secret")
    )
    openalex_ok = bool(s.openalex_enabled and effective_setting(s, "openalex_mailto"))

    # Official patent search is EPO OPS (services.epo_ops) - plain httpx, nothing to install - and the credentials are all
    # it needs; its DOCDB covers the US publications too, so one key serves both "patents" and "epo".
    patents_online = epo_keys
    epo_ok = epo_keys
    # Semantic Scholar is queried over plain HTTP (search_semantic_scholar), so literature search needs nothing
    # installed. This used to probe for the `semanticscholar` SDK that no code imports and no extra installs, which
    # reported "library_missing" - and a "去安装依赖" banner nothing could clear - whenever OpenAlex was switched off.
    lit_ok = True
    web_ok = _ok("ddgs") or _ok("duckduckgo_search")

    return {
        "patents": {
            "available": True,
            "offline_fallback": True,
            "reason": None if patents_online else "offline_seed",
            "hint": (
                None
                if patents_online
                else "官方专利检索（EPO OPS，覆盖 US / EP / WO / CN / JP 等）需要 EPO Consumer Key / Secret"
                "（developers.epo.org 免费注册，填入 设置 → API 配置）；未配置时使用 Google Patents / SerpAPI / "
                "内置种子语料"
            ),
        },
        "literature": {
            "available": lit_ok,
            "offline_fallback": False,
            "reason": None,
            "hint": None,
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
        # Retired 2026-09 (de-ChemCrow): no extra installs it and no code imports it, so there is nothing left to probe - this
        # used to report "installed but incompatible" for a package nothing uses. The key stays because /api/search/status is
        # a public contract; `deprecated` tells clients to stop reading it.
        "chemcrow": {
            "available": False,
            "offline_fallback": False,
            "reason": "retired",
            "deprecated": True,
            "hint": "ChemCrow 集成已于 2026-09 退役，无需安装（化学增强检索由 paper-qa 与网络源提供）",
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
