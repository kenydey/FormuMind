"""Review-run read API + manual re-run (W5-4 / P1-28).

Thin HTTP adapter over ``services.reviewer_fix_loop``. Review runs persist as
JSON under ``data/reviews/runs/`` (P1-13); this module exposes them read-only
plus a manual re-run trigger that reuses the fix-loop entrypoint.

Stale contract (shared with W5-3): run dicts carry
``{"stale": bool | "unverified", "stale_reason": str | None}``; this module
reads them via ``.get`` so it works both before and after W5-3 lands.

Auth is enforced by the global bearer-token middleware, same as the other
routers.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import reviewer_fix_loop

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reviews", tags=["reviews"])

# 列表扫描上限：runs 是小文件 JSON，cap 防止目录膨胀时拖慢请求。
_LIST_SCAN_CAP = 500
_LIST_LIMIT_MAX = 200

_WARN_STATUSES = {"warning", "warn"}
_FAIL_STATUSES = {"failure", "fail"}


def _warn_fail_counts(dispositions: dict) -> tuple[int, int]:
    warn = fail = 0
    for v in (dispositions or {}).values():
        s = str((v or {}).get("status") or "").lower()
        if s in _WARN_STATUSES:
            warn += 1
        elif s in _FAIL_STATUSES:
            fail += 1
    return warn, fail


def _summarize_run(run: dict) -> dict:
    """List/detail 公共摘要：run 字段 + claim 级 disposition 计数。"""
    key = run.get("session_key")
    dispositions = reviewer_fix_loop.load_dispositions(key) if key else {}
    warn, fail = _warn_fail_counts(dispositions)
    return {
        "run_id": run.get("run_id"),
        "session_key": key,
        "project_id": run.get("project_id"),
        "status": run.get("status"),
        "outcome": run.get("outcome"),
        "stale": run.get("stale", False),
        "stale_reason": run.get("stale_reason"),
        "warn_count": warn,
        "fail_count": fail,
        "unaddressed_count": sum(
            1
            for v in (dispositions or {}).values()
            if (v or {}).get("disposition") == "unaddressed"
        ),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
    }


@router.get("/runs")
def list_review_runs(
    session_key: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
) -> dict:
    """List persisted review runs, newest first. Fail-open per file."""
    limit = max(1, min(int(limit or 50), _LIST_LIMIT_MAX))
    # _run_path 是 service 内部路径构造；复用它避免重复实现 sanitize 规则。
    runs_dir = reviewer_fix_loop._run_path("probe").parent  # noqa: SLF001
    items: list[dict] = []
    if runs_dir.is_dir():
        paths = sorted(
            runs_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:_LIST_SCAN_CAP]
        for p in paths:
            try:
                run = reviewer_fix_loop.load_review_run(p.stem)
            except Exception:  # noqa: BLE001
                continue  # 单个文件损坏不影响列表
            if not run:
                continue
            if session_key and run.get("session_key") != session_key:
                continue
            if project_id and run.get("project_id") != project_id:
                continue
            items.append(_summarize_run(run))
            if len(items) >= limit:
                break
    return {"items": items}


@router.get("/runs/{run_id}")
def get_review_run(run_id: str) -> dict:
    """Run 详情：摘要 + claim 级 action log（dispositions）。"""
    run = reviewer_fix_loop.load_review_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="review run not found")
    out = _summarize_run(run)
    key = run.get("session_key")
    out["dispositions"] = (
        reviewer_fix_loop.load_dispositions(key) if key else {}
    )
    return out


class RerunRequest(BaseModel):
    question: str = ""
    answer: str = ""
    citations: list = Field(default_factory=list)
    project_id: str | None = None
    max_rounds: int = 1


@router.post("/runs/{run_id}/rerun")
def rerun_review(run_id: str, body: RerunRequest) -> dict:
    """手动重审：对给定问答复用 review_answer + run_fix_loop 入口。

    run 本体不存问答原文（P1-13 只存元数据），因此 question/answer 由调用方
    （聊天会话）提供。repair 沿用配置 LLM 改写答案，失败回退原答案
    （fail-open）；run_fix_loop 本身永不抛错。
    B-7：配置了 evidence_reviewer_model 后 review_answer 可能抛
    ReviewerModelError（Wave 5 显错契约）→ 转为 503 显式错误响应，
    绝不伪装成"已审"（文档"永不抛错"对该路径已不成立）。
    """
    run = reviewer_fix_loop.load_review_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="review run not found")
    if not (body.question or "").strip() or not (body.answer or "").strip():
        raise HTTPException(
            status_code=400, detail="question and answer are required"
        )
    settings = get_settings()
    try:
        from ..services.evidence_reviewer import ReviewerModelError, review_answer
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"reviewer unavailable: {exc}"
        ) from exc

    try:
        review = review_answer(
            body.question, body.answer, body.citations, settings=settings
        )
    except ReviewerModelError as exc:
        logger.error("rerun reviewer 模型失败: %s", exc)
        raise HTTPException(
            status_code=503, detail=f"reviewer model failed: {exc}"
        ) from exc

    answer_snapshot = body.answer

    def _repair(question: str, auditor_hint: str) -> str:
        try:
            from ..services import llm as _llm

            out = _llm._call_llm(  # noqa: SLF001
                f"{question}\n\n{auditor_hint}\n\n请输出修订后的完整回答：",
                max_tokens=2048,
            )
        except Exception:  # noqa: BLE001
            out = None
        return (out or "").strip() or answer_snapshot

    max_rounds = max(1, min(int(body.max_rounds or 1), 3))
    final_answer, fix = reviewer_fix_loop.run_fix_loop(
        question=body.question,
        answer=body.answer,
        citations=body.citations,
        review=review,
        settings=settings,
        repair_fn=_repair,
        max_rounds=max_rounds,
        project_id=body.project_id or run.get("project_id"),
    )
    return {"review": review, "fix": fix, "final_answer": final_answer}
