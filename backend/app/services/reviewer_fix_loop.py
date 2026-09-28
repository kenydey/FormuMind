"""Bounded Evidence Reviewer fix-loop (AIPOCH-inspired, fail-open for chat).

After ``review_answer`` returns non-pass, append an ``[Auditor]`` note and
re-prompt the chat LLM up to ``max_rounds`` (sync) or 1 (stream).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

ClaimDisposition = dict[str, Any]  # status, reflag_count, disposition


def fix_loop_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "evidence_reviewer_fix_loop_enabled", False))


def _data_root() -> Path:
    return Path("./data").resolve()


def _disposition_path(key: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", key)[:80] or "anon"
    return _data_root() / "reviews" / f"{safe}.json"


def session_key(question: str, answer: str, project_id: str | None = None) -> str:
    raw = f"{project_id or ''}|{question[:200]}|{answer[:400]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load_dispositions(key: str) -> dict[str, ClaimDisposition]:
    path = _disposition_path(key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data.get("claims") or {})
    except Exception:  # noqa: BLE001
        return {}


def save_dispositions(key: str, claims: dict[str, ClaimDisposition]) -> None:
    path = _disposition_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"key": key, "claims": claims, "updated_at": time.time()}
    with _LOCK:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_auditor_note(review: dict[str, Any]) -> str:
    notes = review.get("notes") or []
    suggestion = review.get("suggestion") or ""
    lines = [
        "[Auditor]",
        "上轮回答未通过 Evidence Reviewer。请修订：",
    ]
    for n in notes:
        lines.append(f"- {n}")
    if suggestion:
        lines.append(f"建议：{suggestion}")
    lines.append(
        "要求：删除无据断言，为数值/工艺参数补充 [^n] 引用；"
        "不要编造 DOI；保留已有正确内容。"
    )
    return "\n".join(lines)


def _update_dispositions(
    prev: dict[str, ClaimDisposition],
    review: dict[str, Any],
) -> dict[str, ClaimDisposition]:
    """Track claim-level disposition across rounds (coarse: by note key)."""
    out = dict(prev)
    status = review.get("status") or "pass"
    notes = list(review.get("notes") or [])
    if status == "pass":
        for k, v in list(out.items()):
            if v.get("disposition") != "resolved":
                out[k] = {**v, "status": "pass", "disposition": "resolved"}
        return out
    for note in notes:
        key = note[:80]
        cur = out.get(key) or {"status": status, "reflag_count": 0, "disposition": "open"}
        reflag = int(cur.get("reflag_count") or 0) + 1
        out[key] = {
            "status": status,
            "reflag_count": reflag,
            "disposition": "open",
        }
    return out


def mark_unaddressed(claims: dict[str, ClaimDisposition]) -> dict[str, ClaimDisposition]:
    out = {}
    for k, v in claims.items():
        if v.get("disposition") == "resolved":
            out[k] = v
        elif (v.get("status") or "") in {"failure", "warning", "fail", "warn"}:
            out[k] = {**v, "disposition": "unaddressed"}
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# P1-29 stale review 检测（W5-3）
#
# review 结论与被审 scope 绑定：创建 run 时记录 scope_kind + scope_digest；
# load_review_run 加载时重算 digest，漂移 → stale=True（原 outcome 视为不可信，
# 由 W5-4 前端撤下展示）；无法验证 → stale="unverified"（诚实标记）。
# contract（W5-4 前端消费）：{"stale": bool | "unverified", "stale_reason": str | None}
# ---------------------------------------------------------------------------
_SCOPE_QA = "qa"
_SCOPE_ARTIFACT = "artifact"


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _scope_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _artifact_content_digest(version_id: str) -> str | None:
    """当前 artifact version 的 content sha256；失败返回 None（fail-open）。"""
    try:
        from .artifact_versions import verify_version

        return verify_version(version_id).get("actual_sha256")
    except Exception:  # noqa: BLE001
        return None


def _compute_scope_digest(scope: dict[str, Any]) -> tuple[str, str | None]:
    """返回 (scope_kind, scope_digest)。

    artifact kind：digest = 创建时 artifact content sha256（复用 W4-1
    verify_version）。qa kind：digest = sha256(project_id|question|answer)。
    scope_digest 为 None 表示创建时即无法记录（加载时 → "unverified"）。
    """
    version_id = scope.get("artifact_version_id")
    if version_id:
        return _SCOPE_ARTIFACT, _artifact_content_digest(str(version_id))
    return _SCOPE_QA, _scope_hash(
        {
            "project_id": scope.get("project_id"),
            "question": scope.get("question") or "",
            "answer": scope.get("answer") or "",
        }
    )


def _record_scope(run: dict[str, Any], scope: dict[str, Any] | None) -> dict[str, Any]:
    """把 scope 绑定写入 run（创建时 / fix-loop 结束时重算）。"""
    out = dict(run)
    if not scope:
        out["scope_kind"] = None
        out["scope_digest"] = None
        out["artifact_version_id"] = None
        return out
    kind, digest = _compute_scope_digest(scope)
    out["scope_kind"] = kind
    out["scope_digest"] = digest
    out["artifact_version_id"] = scope.get("artifact_version_id")
    return out
# ---------------------------------------------------------------------------
# P1-13 ReviewRun 生命周期状态机
#
# status: running | complete | error；outcome: pass | flagged | null。
# 与 disposition 同目录（./data/reviews/runs/）JSON 持久化，run 永不抛错。
# ---------------------------------------------------------------------------
_REVIEW_RUN_OUTCOMES = {"pass", "flagged", "null"}


def _run_path(run_id: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", run_id)[:80] or "anon"
    return _data_root() / "reviews" / "runs" / f"{safe}.json"


def new_review_run(
    *,
    session_key: str | None = None,
    project_id: str | None = None,
    scope: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """创建 review run。P1-29: scope 绑定——``scope`` 可含
    ``artifact_version_id`` / ``question`` / ``answer`` / ``project_id``，
    创建时计算 scope_kind + scope_digest 并记录，供 load 时 stale 检测。

    B-8：run_id 并发唯一 —— 毫秒时间戳 + uuid4 随机后缀；
    同一 session 同一毫秒并发建 run 不再互相覆盖审计记录。
    """
    run_id = f"{session_key or 'na'}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:12]}"
    run = {
        "run_id": run_id,
        "session_key": session_key,
        "project_id": project_id,
        "status": "running",
        "outcome": "null",
        "started_at": time.time(),
        "finished_at": None,
        # P1-19 reviewer 模型标签（审计成本可查；None = 沿用全局 llm_model）
        "model": model,
        # P1-29 stale contract（W5-4 前端消费）
        "stale": False,
        "stale_reason": None,
    }
    return _record_scope(run, scope)


def save_review_run(run: dict[str, Any]) -> None:
    path = _run_path(str(run.get("run_id") or "anon"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        path.write_text(
            json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def load_review_run(
    run_id: str, *, scope: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """加载 review run 并做 P1-29 stale 检测。

    - 文件不存在 → None（保持旧行为）。
    - 文件损坏 → ``{"run_id", "stale": "unverified", "stale_reason": ...}``
      （诚实标记"未验证"，不伪装新鲜）。
    - scope_digest 漂移 → ``stale=True`` + ``stale_reason``（原 outcome
      视为不可信，由前端撤下展示）。
    - 无法验证（legacy run 无 scope_digest / qa kind 未提供 scope /
      artifact version 丢失）→ ``stale="unverified"``。

    只读，不回写文件；永不抛错（fail-open）。
    """
    path = _run_path(run_id)
    if not path.is_file():
        return None
    try:
        run = dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return {
            "run_id": run_id,
            "stale": "unverified",
            "stale_reason": "run file unreadable or corrupt",
        }
    try:
        stale, reason = _check_stale(run, scope)
    except Exception:  # noqa: BLE001
        stale, reason = "unverified", "stale check failed"
    run["stale"] = stale
    run["stale_reason"] = reason
    return run


def _check_stale(
    run: dict[str, Any], scope: dict[str, Any] | None
) -> tuple[Any, str | None]:
    """重算 scope digest 并与创建时记录比对。返回 (stale, stale_reason)。"""
    stored = run.get("scope_digest")
    kind = run.get("scope_kind")
    if not stored or not kind:
        return "unverified", "no scope digest recorded (legacy run)"
    if kind == _SCOPE_ARTIFACT:
        version_id = run.get("artifact_version_id")
        current = _artifact_content_digest(str(version_id)) if version_id else None
        if current is None:
            return "unverified", f"artifact version unavailable: {version_id}"
        if current != stored:
            return True, f"artifact content changed since review (version {version_id})"
        return False, None
    # qa kind：用调用方提供的当前 scope 重算比对
    if not scope:
        return "unverified", "scope inputs not provided; cannot verify freshness"
    merged = dict(scope)
    merged.setdefault("project_id", run.get("project_id"))
    _, current = _compute_scope_digest(merged)
    if current != stored:
        return True, "question/answer/project scope changed since review"
    return False, None


def finish_review_run(
    run: dict[str, Any], *, outcome: str = "null", error: bool = False
) -> dict[str, Any]:
    out = dict(run)
    out["outcome"] = outcome if outcome in _REVIEW_RUN_OUTCOMES else "null"
    out["status"] = "error" if error else "complete"
    out["finished_at"] = time.time()
    return out


def _outcome_for_status(status: str | None) -> str:
    s = (status or "").lower()
    if s == "pass":
        return "pass"
    if s in {"warning", "failure", "warn", "fail", "flagged"}:
        return "flagged"
    return "null"


# ---------------------------------------------------------------------------
# P1-12 turn-stop 自动审计：防抖 + per-turn 幂等 + 修正轮抑制
#
# maybe_auto_review 供 turn-stop 钩子调用（chat.py 接线由主流程完成）：
# - 开关 auto_audit_enabled 默认关，按会话 opt-in；
# - 防抖：turn stop 后 100ms 内若有更新的 turn 到达，只触发最后一次；
# - 幂等：同一 turn_id 只触发一次；
# - 抑制：run_fix_loop 执行期间（修正轮）不触发，防自循环。
# ---------------------------------------------------------------------------
_AUTO_DEBOUNCE_S = 0.1
_AUTO_LOCK = threading.Lock()
_AUTO_STATE: dict[str, dict[str, Any]] = {}
# B-11：_AUTO_STATE 有界增长 —— TTL（1h）+ 容量上限（1000，超限淘汰最早活跃）
# + 懒清理（每次进入 maybe_auto_review 时顺带清理）。
_AUTO_STATE_TTL_S = 3600.0
_AUTO_STATE_CAP = 1000


def _auto_state_prune_locked(now: float) -> None:
    """懒清理 _AUTO_STATE。调用方须持有 _AUTO_LOCK。

    - TTL：最后活跃超过 _AUTO_STATE_TTL_S 的 scope 剔除；
    - 容量：仍超上限则按最后活跃时间淘汰最旧，直至不超限。
    """
    expired = [
        k
        for k, st in _AUTO_STATE.items()
        if now - float(st.get("ts", now)) > _AUTO_STATE_TTL_S
    ]
    for k in expired:
        del _AUTO_STATE[k]
    if len(_AUTO_STATE) > _AUTO_STATE_CAP:
        ordered = sorted(
            _AUTO_STATE.items(), key=lambda kv: float(kv[1].get("ts", 0.0))
        )
        for k, _ in ordered[: len(_AUTO_STATE) - _AUTO_STATE_CAP]:
            del _AUTO_STATE[k]
_FIX_LOOP_DEPTH = 0
_FIX_LOOP_GUARD = threading.Lock()


def auto_audit_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "auto_audit_enabled", False))


from contextlib import contextmanager  # noqa: E402


@contextmanager
def _fix_loop_active():
    """标记 fix-loop 执行中；期间 maybe_auto_review 一律抑制。"""
    global _FIX_LOOP_DEPTH
    with _FIX_LOOP_GUARD:
        _FIX_LOOP_DEPTH += 1
    try:
        yield
    finally:
        with _FIX_LOOP_GUARD:
            _FIX_LOOP_DEPTH -= 1


def maybe_auto_review(
    turn_id: str,
    *,
    question: str,
    answer: str,
    citations: list[Any],
    settings: Any,
    repair_fn: Callable[[str, str], str] | None = None,
    session_id: str | None = None,
    project_id: str | None = None,
    max_rounds: int = 1,
) -> dict[str, Any] | None:
    """turn-stop 自动审计入口。

    返回 None = 未触发（关闭/重复/防抖取代/修正轮抑制/无错）；
    否则返回 {"turn_id", "review", "fix"}。
    """
    if not auto_audit_enabled(settings) or not (turn_id or "").strip():
        return None
    scope = session_id or project_id or "default"
    now = time.time()
    with _AUTO_LOCK:
        if _FIX_LOOP_DEPTH > 0:
            return None  # 修正轮抑制
        _auto_state_prune_locked(now)  # B-11：懒清理（TTL + 容量上限）
        st = _AUTO_STATE.setdefault(
            scope, {"pending": None, "fired": set(), "ts": now}
        )
        if turn_id in st["fired"]:
            return None  # per-turn 幂等
        st["pending"] = turn_id
        st["ts"] = now  # 最后活跃
    time.sleep(_AUTO_DEBOUNCE_S)
    with _AUTO_LOCK:
        st = _AUTO_STATE.get(scope)
        if not st or st.get("pending") != turn_id:
            return None  # 防抖：被更新的 turn 取代
        st["fired"].add(turn_id)
        st["pending"] = None
        st["ts"] = time.time()  # 最后活跃
    try:
        from .evidence_reviewer import review_answer

        review = review_answer(question, answer, citations, settings=settings)
    except Exception as exc:  # noqa: BLE001
        logger.debug("auto review skipped: %s", exc)
        return None
    if not review or (review.get("status") or "pass") == "pass":
        return {"turn_id": turn_id, "review": review, "fix": None}
    fix = None
    if repair_fn is not None:
        _, fix = run_fix_loop(
            question=question,
            answer=answer,
            citations=citations,
            review=review,
            settings=settings,
            repair_fn=repair_fn,
            max_rounds=max_rounds,
            project_id=project_id,
        )
    return {"turn_id": turn_id, "review": review, "fix": fix}


def run_fix_loop(
    *,
    question: str,
    answer: str,
    citations: list[Any],
    review: dict[str, Any] | None,
    settings: Any,
    repair_fn: Callable[[str, str], str],
    max_rounds: int = 3,
    project_id: str | None = None,
    artifact_version_id: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Run bounded repair. Returns (final_answer, reviewer_fix meta).

    ``repair_fn(question, auditor_augmented_prompt_hint) -> new_answer``.
    Fail-open: on any error returns original answer + partial meta.
    P1-13: 永不抛错；ReviewRun（running/complete/error × pass/flagged/null）
    与 disposition 同目录 JSON 持久化。
    P1-29: run 绑定 scope（artifact_version_id / question+answer+project），
    load 时可做 stale 检测；scope 以最终答案重算（被审的是最终答案）。
    """
    if not fix_loop_enabled(settings) or not review:
        return answer, None
    if (review.get("status") or "pass") == "pass":
        return answer, {
            "rounds": 0,
            "findings": review,
            "dispositions": {},
            "status": "pass",
        }

    from .evidence_reviewer import reviewer_model_name

    run = new_review_run(
        project_id=project_id,
        model=reviewer_model_name(settings),
        scope={
            "project_id": project_id,
            "question": question,
            "answer": answer,
            "artifact_version_id": artifact_version_id,
        },
    )
    try:
        with _fix_loop_active():
            key = session_key(question, answer, project_id)
            run["session_key"] = key
            # B-8：run_id 并发唯一（毫秒时间戳 + uuid4 随机后缀），防并发覆盖
            run["run_id"] = f"{key}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:12]}"
            save_review_run(run)  # running 状态先落盘
            dispositions = load_dispositions(key)
            current = answer
            last_review = review
            rounds_done = 0
            history_findings: list[dict[str, Any]] = [review]

            from .evidence_reviewer import review_answer

            for _ in range(max(1, int(max_rounds))):
                if (last_review.get("status") or "pass") == "pass":
                    break
                auditor = build_auditor_note(last_review)
                try:
                    repaired = repair_fn(question, auditor)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("fix-loop repair_fn failed: %s", exc)
                    break
                if not (repaired or "").strip() or repaired.strip() == current.strip():
                    break
                current = repaired
                rounds_done += 1
                nxt = review_answer(question, current, citations, settings=settings) or last_review
                history_findings.append(nxt)
                dispositions = _update_dispositions(dispositions, nxt)
                last_review = nxt
                if (nxt.get("status") or "pass") == "pass":
                    dispositions = _update_dispositions(dispositions, nxt)
                    break

            if (last_review.get("status") or "pass") != "pass":
                dispositions = mark_unaddressed(dispositions)
            save_dispositions(key, dispositions)

            unaddressed = [
                k for k, v in dispositions.items() if v.get("disposition") == "unaddressed"
            ]
            # Soft note for preflight merge (optional MVP)
            if unaddressed and project_id:
                _write_soft_note(project_id, unaddressed, last_review)

            run = _record_scope(
                run,
                {
                    "project_id": project_id,
                    "question": question,
                    "answer": current,
                    "artifact_version_id": artifact_version_id,
                },
            )
            run = finish_review_run(run, outcome=_outcome_for_status(last_review.get("status")))
            save_review_run(run)
            return current, {
                "rounds": rounds_done,
                "findings": last_review,
                "findings_history": history_findings,
                "dispositions": dispositions,
                "status": last_review.get("status"),
                "unaddressed": unaddressed,
                "session_key": key,
                "run_id": run["run_id"],
                "run_status": run["status"],
                "run_outcome": run["outcome"],
            }
    except Exception as exc:  # noqa: BLE001
        logger.debug("fix-loop failed open: %s", exc)
        try:
            run = finish_review_run(run, outcome="null", error=True)
            save_review_run(run)
        except Exception:  # noqa: BLE001
            logger.debug("review run persist failed")
        return answer, {
            "rounds": 0,
            "findings": review,
            "dispositions": {},
            "status": "error",
            "error": str(exc)[:200],
            "run_id": run.get("run_id"),
            "run_status": "error",
            "run_outcome": "null",
        }


def _write_soft_note(project_id: str, unaddressed: list[str], review: dict[str, Any]) -> None:
    try:
        safe = re.sub(r"[^\w.\-]+", "_", project_id)[:80]
        path = _data_root() / "preflight" / safe / "evidence_soft.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "project_id": project_id,
            "at": time.time(),
            "unaddressed": unaddressed,
            "review": {
                "status": review.get("status"),
                "notes": review.get("notes"),
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("soft note write skipped: %s", exc)
