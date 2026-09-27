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
) -> tuple[str, dict[str, Any] | None]:
    """Run bounded repair. Returns (final_answer, reviewer_fix meta).

    ``repair_fn(question, auditor_augmented_prompt_hint) -> new_answer``.
    Fail-open: on any error returns original answer + partial meta.
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

    key = session_key(question, answer, project_id)
    dispositions = load_dispositions(key)
    current = answer
    last_review = review
    rounds_done = 0
    history_findings: list[dict[str, Any]] = [review]

    try:
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

        return current, {
            "rounds": rounds_done,
            "findings": last_review,
            "findings_history": history_findings,
            "dispositions": dispositions,
            "status": last_review.get("status"),
            "unaddressed": unaddressed,
            "session_key": key,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("fix-loop failed open: %s", exc)
        return answer, {
            "rounds": rounds_done,
            "findings": last_review,
            "dispositions": dispositions,
            "status": "error",
            "error": str(exc)[:200],
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
