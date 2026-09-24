"""KB retrieval probe (query-test) orchestration.

Powers ``POST /api/kb/query-test`` and golden batch runs for the Knowledge Hub
retrieval workbench. Keeps production ``/hybrid-search`` unscored.
"""
from __future__ import annotations

import time
from typing import Any, Literal

from ..config import get_settings
from ..domain.schemas import Evidence
from . import kb_index
from .hybrid_search import hybrid_search_scored
from .rag import llm_rerank_scored

QueryTestMode = Literal["keyword", "hybrid", "hybrid_rerank"]


def _snippet(text: str, limit: int = 400) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"


def _title_for_chunk(chunk, source_meta: dict) -> str:
    meta = source_meta.get(chunk.source_id) or {}
    title = meta.get("title") or "知识库文档"
    if getattr(chunk, "heading_path", None):
        title = f"{title} · {chunk.heading_path}"
    if getattr(chunk, "page_no", None):
        title = f"{title} · P{chunk.page_no}"
    return title[:200]


def _parse_kb_identifier(identifier: str) -> tuple[str | None, int | None]:
    """Parse ``kb:{source_id}#c{ord}`` → (source_id, ord)."""
    if not identifier or not str(identifier).startswith("kb:"):
        return None, None
    body = str(identifier)[3:]
    if "#c" in body:
        source_id, _, ord_s = body.partition("#c")
        try:
            return source_id or None, int(ord_s)
        except ValueError:
            return source_id or None, None
    return body or None, None


def run_query_test(
    *,
    query: str,
    mode: QueryTestMode = "hybrid",
    top_k: int = 10,
    alpha: float | None = None,
    project_id: str | None = None,
    include_global: bool = True,
    rerank: bool | None = None,
) -> dict[str, Any]:
    """Execute a scored retrieval probe and return a JSON-serializable payload."""
    t0 = time.perf_counter()
    settings = get_settings()
    q = (query or "").strip()
    top_k = max(1, min(int(top_k), 50))
    if alpha is None:
        alpha = float(settings.kb_hybrid_alpha)
    if alpha < 0.0 or alpha > 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")

    stats = kb_index.kb_stats()
    vector_mode = str(stats.get("vector_mode") or "empty")

    def _gate_payload(before: dict[str, int] | None = None) -> dict[str, Any]:
        from .kb_retrieval_gate import gate_drop_delta, gate_drop_stats

        total = gate_drop_stats()
        run = gate_drop_delta(before or {}) if before is not None else {
            "retrieval": {"blocked_domain": 0, "garbage_snippet": 0, "wiki_track": 0},
            "ingest": {"blocked_domain": 0, "garbage_snippet": 0, "wiki_track": 0},
        }
        return {"gate_drops": run, "gate_drops_total": total}

    def _done(
        hits: list[dict[str, Any]],
        *,
        rerank_applied: bool = False,
        warning: str | None = None,
        gate_before: dict[str, int] | None = None,
    ):
        payload = {
            "query": q,
            "mode": mode,
            "params": {
                "top_k": top_k,
                "alpha": alpha,
                "project_id": project_id,
                "include_global": include_global,
                "rerank_applied": rerank_applied,
            },
            "vector_mode": vector_mode,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "hits": hits,
            "warning": warning,
        }
        payload.update(_gate_payload(gate_before))
        return payload

    if not kb_index.kb_enabled():
        return _done([], warning="知识库 v2 未启用")
    if not q:
        return _done([], warning="query 为空")

    source_meta = kb_index._source_meta()

    if mode == "keyword":
        evidence = kb_index.search_chunks(
            q,
            k=top_k,
            project_id=project_id,
            include_global=include_global,
        )
        hits: list[dict[str, Any]] = []
        for rank, ev in enumerate(evidence, start=1):
            source_id, ord_v = _parse_kb_identifier(ev.identifier)
            hits.append(
                {
                    "rank": rank,
                    "chunk_id": None,
                    "source_id": source_id,
                    "ord": ord_v,
                    "title": ev.title,
                    "snippet": _snippet(ev.snippet),
                    "bm25_score": None,
                    "cosine_score": None,
                    "hybrid_score": None,
                    "relevance": float(ev.relevance),
                    "rerank_score": None,
                    "rank_before_rerank": None,
                    "meta": None,
                }
            )
        return _done(hits)

    from .kb_retrieval_gate import gate_drop_snapshot

    gate_before = gate_drop_snapshot()

    want_rerank = False
    if mode == "hybrid_rerank":
        want_rerank = bool(settings.search_rerank_enabled) if rerank is None else bool(rerank)

    pool_k = top_k
    if want_rerank:
        pool_k = max(top_k, min(int(settings.search_rerank_llm_batch or 50), 50))

    scored = hybrid_search_scored(
        q,
        top_k=pool_k,
        alpha=alpha,
        project_id=project_id,
        include_global=include_global,
    )

    prelim: list[dict[str, Any]] = []
    evidence_pool: list[Evidence] = []
    for idx, sc in enumerate(scored):
        c = sc.chunk
        title = _title_for_chunk(c, source_meta)
        ev = Evidence(
            source=(source_meta.get(c.source_id) or {}).get("source_kind") or "kb",
            identifier=f"kb:{c.source_id}#c{c.ord}",
            title=title,
            snippet=kb_index.chunk_snippet(c.text),
            relevance=max(0.05, min(1.0, float(sc.hybrid_score))),
        )
        evidence_pool.append(ev)
        prelim.append(
            {
                "rank": idx + 1,
                "chunk_id": getattr(c, "id", None),
                "source_id": c.source_id,
                "ord": c.ord,
                "title": title,
                "snippet": _snippet(c.text),
                "bm25_score": sc.bm25_score,
                "cosine_score": sc.cosine_score,
                "hybrid_score": sc.hybrid_score,
                "relevance": max(0.05, min(1.0, float(sc.hybrid_score))),
                "rerank_score": None,
                "rank_before_rerank": idx + 1,
                "meta": getattr(c, "meta", None),
            }
        )

    if mode == "hybrid" or not want_rerank:
        warning = None
        if mode == "hybrid_rerank" and not want_rerank:
            warning = "重排未启用（search_rerank_enabled=false 或请求覆盖为 false）"
        return _done(prelim[:top_k], warning=warning, gate_before=gate_before)

    items, applied = llm_rerank_scored(q, evidence_pool, k=top_k)
    if not applied:
        return _done(
            prelim[:top_k],
            rerank_applied=False,
            warning="LLM 重排失败或未返回分数，已回退 hybrid 排序",
            gate_before=gate_before,
        )

    hits = []
    for new_rank, item in enumerate(items, start=1):
        base_row = prelim[item.original_index]
        row = dict(base_row)
        row["rank"] = new_rank
        row["rerank_score"] = round(float(item.score), 6)
        row["relevance"] = max(0.05, min(1.0, float(item.score)))
        hits.append(row)
    return _done(hits, rerank_applied=True, gate_before=gate_before)


def run_golden_eval(
    *,
    mode: QueryTestMode = "hybrid",
    top_k: int = 3,
    alpha: float | None = None,
    project_id: str | None = None,
    include_global: bool = True,
    rerank: bool | None = None,
) -> dict[str, Any]:
    """Run golden questions; keyword-hit@k plus Recall@k / MRR.

    Recall@k = fraction of questions with ≥1 expected keyword in top-k texts.
    MRR = mean reciprocal rank of the first hit that contains any expected keyword
    (0 when none match in top-k).
    """
    from ..resources.golden_retrieval import golden_questions

    if alpha is None:
        alpha = float(get_settings().kb_hybrid_alpha)
    results: list[dict[str, Any]] = []
    passed = 0
    reciprocal_ranks: list[float] = []
    for entry in golden_questions:
        q = entry["question"]
        expected = list(entry.get("expected_keywords") or [])
        payload = run_query_test(
            query=q,
            mode=mode,
            top_k=top_k,
            alpha=alpha,
            project_id=project_id,
            include_global=include_global,
            rerank=rerank,
        )
        hits = list(payload.get("hits") or [])
        metrics = keyword_rank_metrics(hits, expected, top_k=top_k)
        ok = bool(metrics["hit"])
        if ok:
            passed += 1
        reciprocal_ranks.append(float(metrics["reciprocal_rank"]))
        results.append(
            {
                "question": q,
                "category": entry.get("min_relevance_category") or "",
                "passed": ok,
                "matched_keyword": metrics["matched_keyword"],
                "first_hit_rank": metrics["first_hit_rank"],
                "reciprocal_rank": metrics["reciprocal_rank"],
                "expected_keywords": expected,
                "hit_titles": [h.get("title") for h in hits[:top_k]],
                "elapsed_ms": payload.get("elapsed_ms"),
                "warning": payload.get("warning"),
            }
        )

    total = len(results)
    recall_at_k = (passed / total) if total else 0.0
    mrr = (sum(reciprocal_ranks) / total) if total else 0.0
    return {
        "mode": mode,
        "top_k": top_k,
        "alpha": alpha,
        "project_id": project_id,
        "include_global": include_global,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "recall_at_k": round(recall_at_k, 4),
        "mrr": round(mrr, 4),
        "results": results,
    }


def keyword_rank_metrics(
    hits: list[dict[str, Any]],
    expected_keywords: list[str],
    *,
    top_k: int = 3,
) -> dict[str, Any]:
    """Pure helper: first-hit rank / RR / matched keyword for expected KWs."""
    expected = [kw for kw in expected_keywords if kw]
    first_rank: int | None = None
    matched: str | None = None
    for i, h in enumerate(hits[:top_k], start=1):
        blob = f"{h.get('title') or ''} {h.get('snippet') or ''} {h.get('text') or ''}"
        for kw in expected:
            if kw in blob:
                first_rank = i
                matched = kw
                break
        if first_rank is not None:
            break
    rr = (1.0 / first_rank) if first_rank else 0.0
    return {
        "hit": first_rank is not None,
        "first_hit_rank": first_rank,
        "reciprocal_rank": rr,
        "matched_keyword": matched,
    }


def list_golden_questions() -> list[dict[str, Any]]:
    from ..resources.golden_retrieval import golden_questions

    return [
        {
            "question": e["question"],
            "expected_keywords": list(e.get("expected_keywords") or []),
            "category": e.get("min_relevance_category") or "",
        }
        for e in golden_questions
    ]
