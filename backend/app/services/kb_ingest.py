"""Async knowledge-base ingest queue — search results → full text → KB.

The search path streams abstract-level hits to the frontend immediately; this
module is the *background* half of the pipeline.  A dispatched ingest job
walks the fetchable evidence rows **one by one** (per-document status is more
useful to the UI than raw throughput, and sequential fetching is polite to
upstream sites):

    queued → fetching → indexing → indexed | skipped | failed | unsupported

Every transition is reported through ``status_cb`` so the Celery task can
publish SSE events; the frontend paints per-document badges from them.

Reuses the fulltext_fetcher registry (patent PDF / OA literature PDF / web
page) and the standard persistence path (``SourceDocument`` + ``kb_index``),
so parsing, chunking and embedding behave exactly like every other ingest
route.  Dedup is two-tier: ``origin_url`` before the download is attempted,
content hash afterwards (inside ``_persist_fulltext``).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from ..config import get_settings
from ..domain.schemas import Evidence
from . import ingest_timing as timing
from .errors import degrade_return

logger = logging.getLogger(__name__)

# Terminal per-document states (anything else is transient).
TERMINAL_STATES = frozenset({"indexed", "skipped", "failed", "unsupported"})

StatusCb = Callable[[dict[str, Any]], None]


def ingest_enabled() -> bool:
    """Auto-dispatch switch: async KB build after search / research tasks."""
    settings = get_settings()
    return bool(settings.kb_ingest_auto and settings.kb_v2_enabled)


# ── 主题预筛(永久规则 2026-09) ─────────────────────────────────────────────
# 背景: 自动入库按 relevance+fetchable 全收, 会吞进磁性/储氢/电池/半导体/
# 天文/纯力学等与"金属表面处理/配方研发"无关的检索结果(2026-09-04 一次吞 29 篇)。
# 规则: 项目标题含领域锚词时, 入库前按 高判别词≥1 或 低判别词≥2 放行,
# 反向硬拦词命中且高判别词<2 时拦截。专利豁免(主动 IP 检索), 无锚词不过滤。

# 高判别词(工艺/功能/配方/腐蚀领域)——命中任一即视为领域内(子串匹配, stem 安全)
_TOPIC_HIGH = [
    # 中文
    "钝化", "转化膜", "防腐", "防锈", "涂层", "涂料", "树脂", "乳液", "聚合物",
    "磷化", "缓蚀", "清洗", "脱脂", "预处理", "电泳", "阳极氧化", "喷涂",
    "表面处理", "成膜", "硅烷", "溶胶", "漆", "固化剂", "环氧", "聚氨酯",
    "丙烯酸", "粘结剂", "颜料", "助剂", "盐雾", "防腐蚀",
    # 英文(词干, 子串匹配)
    "corrosi", "passivat", "coating", "paint", "resin", "emulsion", "polymer",
    "conversion", "phosphat", "chromat", "inhibit", "pretreat", "pickling",
    "degreas", "anodiz", "electrodeposit", "primer", "adhesion", "sol-gel",
    "silane", "curing", "epoxy", "polyurethane", "acrylic", "binder",
    "pigment", "formulation", "salt spray", "surface treatment",
]
# 低判别词(基材/金属/合金)——需 ≥2 才放行
_TOPIC_LOW = [
    "镁", "铝", "锌", "钢", "铁", "铜", "钛", "镍", "合金", "金属",
    "底材", "基材", "不锈钢", "碳钢",
    "magnesium", "aluminum", "aluminium", "zinc", "steel", "iron", "copper",
    "titanium", "nickel", "alloy", "metal", "substrate", "stainless",
]
# 反向硬拦(领域外强信号)——命中且高判别词 <2 → 拦截
_TOPIC_BLOCK = [
    "磁性", "超导", "电池", "半导体", "光伏", "天文", "星际", "核聚变",
    "生物医学", "植入", "可降解", "硬化", "位错", "疲劳", "储氢", "高熵",
    "刀具", "准晶",
    "magnetic", "magnetoresist", "superconduct", "battery", "electrode",
    "photovoltaic", "perovskite", "semiconductor", "astronom", "interstellar",
    "gravitat", "nuclear", "fusion", "quasicrystal", "biomedical", "implant",
    "biodegrad", "dental", "dislocation", "hardening", "tensile", "creep",
    "fatigue", "high-entropy", "nanolamellar", "electrolyzer", "hydride",
    "heusler", "quasar", "doping", "hydrogen storage", "cutting",
]
# 需要词边界而非子串的短反向词(避免 hall∈shall 之类误伤)
_TOPIC_BLOCK_WORD = ["hall", "doping"]


def _topic_anchor(project_id: str | None = None, query: str | None = None) -> tuple[set[str], set[str]] | None:
    """项目标题/副标题(缺省时用检索 query)中命中的领域词 → (高判别锚, 低判别锚)。

    返回 None = 无领域锚词(或查不到项目)——调用方应跳过过滤(宁吞勿误杀)。
    search API 不带 project 上下文时, query 即本次检索意图的锚。
    """
    text = ""
    if project_id:
        try:
            from ..db.database import default_session_factory
            from ..db.models import Project

            with default_session_factory()() as session:
                proj = session.get(Project, project_id)
                if proj is not None:
                    text = " ".join(
                        str(x) for x in (proj.title, proj.headline) if x
                    )
        except Exception:
            logger.warning("kb_ingest topic anchor lookup failed for %s", project_id, exc_info=True)
            return None
    if not text:
        text = query or ""
    text = text.lower()
    high = {w for w in _TOPIC_HIGH if w in text}
    low = {w for w in _TOPIC_LOW if w in text}
    if not high and not low:
        logger.info(
            "kb_ingest topic filter: 锚文本(project=%s)无领域词,跳过过滤",
            bool(project_id),
        )
        return None
    return high, low


def _topic_hits(text: str) -> tuple[int, int, int]:
    """(高判别命中数, 低判别命中数, 反向命中数)——每词计一次, 词内去重。"""
    n_high = sum(1 for w in _TOPIC_HIGH if w in text)
    n_low = sum(1 for w in _TOPIC_LOW if w in text)
    n_block = sum(1 for w in _TOPIC_BLOCK if w in text)
    for w in _TOPIC_BLOCK_WORD:  # 词边界版
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text):
            n_block += 1
    return n_high, n_low, n_block


def topic_gate(evidence_text: str, *, kind: str | None = None) -> bool:
    """领域相关性判定(不含项目锚前置检查)。

    kind='patent' 豁免; 高判别≥1 放行; 低判别≥2 放行; 反向命中且高判别<2 拦截。
    """
    if kind == "patent":
        return True
    text = (evidence_text or "").lower()
    n_high, n_low, n_block = _topic_hits(text)
    if n_block and n_high < 2:
        return False
    if n_high >= 1:
        return True
    if n_low >= 2:
        return True
    return False


def _doc_meta(ev: Evidence, kind: str | None) -> dict[str, Any]:
    return {
        "identifier": ev.identifier,
        "title": (ev.title or ev.identifier)[:200],
        "kind": kind or "unsupported",
        "status": "queued",
        "source_id": None,
        "error": None,
    }


def select_ingest_targets(
    evidence: list[Evidence], *,
    max_docs: int | None = None, project_id: str | None = None, query: str | None = None,
) -> list[tuple[Evidence, str]]:
    """The top fetchable rows in rank order, as (evidence, kind) pairs.

    Applies the topic pre-filter (permanent rule 2026-09): when the project
    (or the search query, when no project context exists) carries domain
    anchor words and the filter is enabled, off-domain rows (magnetism /
    hydrides / batteries / semiconductors / astronomy / pure mechanics …) are
    dropped before fetching.  Rows are only skipped with a log line; the
    user-facing search result list is untouched.
    """
    from . import fulltext_fetcher as ff

    # 0 (or None) means no cap: every fetchable hit is ingested. "Searched but
    # not stored" reads as data loss to whoever ran the search, so the default
    # is to store everything and let the operator opt into a limit.
    limit = max_docs if max_docs is not None else get_settings().kb_ingest_max_docs
    min_rel = get_settings().kb_ingest_min_relevance
    topic_filter = bool(get_settings().kb_ingest_topic_filter)
    anchor = _topic_anchor(project_id, query) if topic_filter else None
    targets: list[tuple[Evidence, str]] = []
    seen: set[str] = set()
    for ev in evidence:
        if limit and len(targets) >= limit:
            break
        if min_rel > 0 and (ev.relevance or 0) < min_rel:
            continue
        ident = (ev.identifier or "").strip()
        if not ident or ident in seen:
            continue
        kind = ff.classify(ev)
        if kind:
            if anchor is not None:
                text = " ".join(
                    str(x)
                    for x in (ev.title, ev.snippet, ev.identifier)
                    if x
                )
                if not topic_gate(text, kind=kind):
                    logger.warning(
                        "kb_ingest 主题预筛拦截: %s (%s)",
                        (ev.title or ev.identifier)[:90],
                        ev.identifier,
                    )
                    continue
            targets.append((ev, kind))
            seen.add(ident)
    return targets


def _fetch_one(
    ev: Evidence, kind: str, timeout: float, emit: StatusCb, doc: dict[str, Any]
) -> str | None:
    """Acquire one document's full text. Network-bound; safe to run in parallel.

    Returns the text, or None when the document is already known (status
    ``skipped``) or unobtainable (status ``failed``). Mutates *doc*.
    """
    from ..db.source_store import get_source_store
    from . import fulltext_fetcher as ff

    ident = (ev.identifier or "").strip()

    # Dedup tier 1: this URL / patent id / DOI was already acquired.
    try:
        existing = get_source_store().find_by_origin_url(ident)
    except Exception as exc:
        existing = degrade_return(logger, exc, "kb_ingest dedup lookup failed", None)
    if existing is not None:
        doc.update(status="skipped", source_id=existing.id)
        emit(doc)
        return None

    doc["status"] = "fetching"
    emit(doc)
    fetch_reason: str | None = None
    try:
        with timing.span("fetch"):
            text = ff._dispatch_fetch(kind, ev, timeout)
    except ff.FetchError as fe:
        text = None
        fetch_reason = fe.reason
    except Exception as exc:
        text = degrade_return(logger, exc, f"kb_ingest fetch failed ({kind})", None)
    # Recorded even when empty: "how much did the web channel actually return"
    # is the question, and a zero is as much of an answer as a large number.
    timing.note(chars=len(text or ""))
    if not text:
        error = (
            f"全文获取失败（{fetch_reason}）"
            if fetch_reason
            else "全文获取失败（无 OA 版本 / 下载超时 / 解析为空）"
        )
        doc.update(status="failed", error=error)
        emit(doc)
        return None
    return text


def _index_one(
    text: str,
    ev: Evidence,
    kind: str,
    emit: StatusCb,
    doc: dict[str, Any],
    *,
    project_id: str | None = None,
) -> None:
    """Chunk, embed and persist one document. Kept serial — SQLite writers."""
    from . import fulltext_fetcher as ff

    doc["status"] = "indexing"
    emit(doc)
    source_id = ff._persist_fulltext(text, ev, kind, project_id=project_id)  # hash-dedup + chunk + embed inside
    if source_id:
        doc.update(status="indexed", source_id=source_id)
    else:
        doc.update(status="failed", error="入库失败（存储或索引异常）")
    emit(doc)


def _ingest_one(ev: Evidence, kind: str, timeout: float, emit: StatusCb, doc: dict[str, Any], *, project_id: str | None = None) -> None:
    """Advance one document through the state machine (mutates *doc*)."""
    text = _fetch_one(ev, kind, timeout, emit, doc)
    if text:
        _index_one(text, ev, kind, emit, doc, project_id=project_id)


def _backfill_product_structures() -> dict:
    """Resolve 牌号 → CAS/SMILES for the batch, once per product.

    Never raises and never fails the ingest: every document is already chunked,
    embedded and searchable by this point. The only thing missing until this
    finishes is synonym expansion when a *query* names a trade name — a recall
    enhancement, read at query time, so it starts working the moment this lands
    without any reindex.
    """
    settings = get_settings()
    if not settings.product_extract_enabled:
        return {"attempted": 0, "resolved": 0, "mentions_covered": 0}
    try:
        from ..db.product_store import get_product_store

        return get_product_store().backfill_structures()
    except Exception as exc:
        return degrade_return(
            logger, exc, "product structure backfill failed",
            {"attempted": 0, "resolved": 0, "mentions_covered": 0},
        )


def ingest_evidence_docs(
    evidence: list[Evidence],
    *,
    max_docs: int | None = None,
    status_cb: StatusCb | None = None,
    project_id: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Sequentially acquire + index the fetchable subset of *evidence*.

    Returns a summary dict (also the Celery task result):
    ``{"docs": [...], "total", "indexed", "skipped", "failed"}``.
    Never raises — one bad document must not kill the rest of the queue.
    """
    import concurrent.futures

    settings = get_settings()
    emit: StatusCb = status_cb or (lambda meta: None)
    timeout = float(settings.fulltext_timeout_s)
    targets = select_ingest_targets(evidence, max_docs=max_docs, project_id=project_id, query=query)

    docs = [_doc_meta(ev, kind) for ev, kind in targets]
    for doc in docs:  # announce the full queue up front
        emit(doc)

    # Fetch concurrently, index serially, and pipeline the two. Fetching is
    # network-bound and is the whole cost of a large batch — 340 documents one
    # at a time, at a 20 s timeout each, is over an hour. Indexing stays on one
    # thread because it writes SQLite and because a fetch may run the PDF
    # parser cascade, whose peak (~350 MB, or ~557 MB when a scan goes through
    # OCR) is what bounds the worker count on a small host — not the sockets.
    workers = max(1, int(getattr(settings, "kb_ingest_workers", 3)))

    def _fetch(i: int) -> tuple[int, str | None]:
        ev, kind = targets[i]
        try:
            # `track` spans threads: this runs in a worker, the matching index
            # phase runs on the main thread, and both land in one record.
            with timing.track(ev.identifier or "", kind):
                return i, _fetch_one(ev, kind, timeout, emit, docs[i])
        except Exception as exc:
            degrade_return(logger, exc, "kb_ingest fetch failed", None)
            docs[i].update(status="failed", error=str(exc)[:200])
            return i, None

    def _index(i: int, text: str | None) -> None:
        ev, kind = targets[i]
        if text:
            try:
                with timing.track(ev.identifier or "", kind):
                    _index_one(text, ev, kind, emit, docs[i], project_id=project_id)
            except Exception as exc:  # one bad document must not kill the queue
                degrade_return(logger, exc, "kb_ingest document failed", None)
                docs[i].update(status="failed", error=str(exc)[:200])
        timing.finish(ev.identifier or "", docs[i]["status"])

    with timing.batch("kb_ingest"):
        if workers == 1 or len(targets) <= 1:
            for i in range(len(targets)):
                _index(*_fetch(i))
        else:
            # `as_completed`, not `ex.map`: map returns an iterator that yields
            # in submission order, so one slow download at the head held back
            # every document behind it and nothing was indexed until the last
            # fetch finished. It also meant every fetched full text — up to
            # several hundred documents — sat in memory at once waiting for
            # the indexing phase to start.
            #
            # Indexing now happens in completion order. Content-hash dedup
            # therefore resolves to whichever of two byte-identical documents
            # finished first rather than to the higher-ranked one; the stored
            # text is the same either way, only the recorded origin_url differs.
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_fetch, i): i for i in range(len(targets))}
                for fut in concurrent.futures.as_completed(futures):
                    try:
                        idx, text = fut.result()
                    except Exception as exc:  # _fetch catches, but never trust that
                        idx = futures[fut]
                        degrade_return(logger, exc, "kb_ingest fetch crashed", None)
                        docs[idx].update(status="failed", error=str(exc)[:200])
                        text = None
                    _index(idx, text)

    # Structure linking was deferred out of every document (see
    # `kb_index._attach_entities`); resolve it once per product now that the
    # batch is done. Outside `timing.batch` on purpose — it is not part of any
    # document's cost and folding it in would misattribute it.
    structures = _backfill_product_structures()

    summary = {
        "docs": docs,
        "total": len(docs),
        "indexed": sum(1 for d in docs if d["status"] == "indexed"),
        "skipped": sum(1 for d in docs if d["status"] == "skipped"),
        "failed": sum(1 for d in docs if d["status"] == "failed"),
        "product_structures": structures,
    }
    logger.info(
        "kb_ingest: %d indexed / %d skipped / %d failed of %d",
        summary["indexed"], summary["skipped"], summary["failed"], summary["total"],
    )
    return summary
