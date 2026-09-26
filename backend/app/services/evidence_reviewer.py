"""Lightweight Evidence Reviewer — second-pass claim↔citation check."""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def reviewer_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "evidence_reviewer_enabled", False))


def review_answer(
    question: str,
    answer: str,
    citations: list[Any],
    *,
    settings: Any,
) -> dict[str, Any] | None:
    """Heuristic + optional LLM nudge. Fail-open (returns None on errors)."""
    if not reviewer_enabled(settings) or not (answer or "").strip():
        return None
    try:
        from .chat_claims import build_sourced_claims

        claims = build_sourced_claims(
            question,
            answer,
            citations,
            structured=None,
            settings=settings,
        ) or []
        unsupported = [c for c in claims if getattr(c, "status", None) == "unsupported"]
        weak = [c for c in claims if getattr(c, "status", None) == "weak"]
        # Also flag invented-looking DOIs already marked in answer footer
        doi_warn = "DOI 校验" in (answer or "")
        status = "pass"
        if unsupported or doi_warn:
            status = "failure" if unsupported else "warning"
        elif weak:
            status = "warning"
        notes: list[str] = []
        if unsupported:
            notes.append(f"{len(unsupported)} 条断言无据")
        if weak:
            notes.append(f"{len(weak)} 条弱支撑")
        if doi_warn:
            notes.append("DOI 校验提出警告")
        suggestion = None
        if status != "pass":
            suggestion = (
                "请收紧表述：删除无据断言，或补充可跳转来源；"
                "数值工艺参数必须带来源。"
            )
        return {
            "status": status,
            "notes": notes,
            "suggestion": suggestion,
            "unsupported_count": len(unsupported),
            "weak_count": len(weak),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("evidence reviewer skipped: %s", exc)
        return None


_NUMERIC_BARE = re.compile(
    r"(?<!\[[^\]]{0,40})\b(\d+(?:\.\d+)?\s*(?:wt%|%|℃|°C|MPa|μm|µm|hrs?|h|min))\b"
)


def flag_bare_numerics(answer: str) -> list[str]:
    """Soft signal: numerics with units that lack nearby citation markers."""
    hits: list[str] = []
    for m in _NUMERIC_BARE.finditer(answer or ""):
        start = max(0, m.start() - 40)
        window = answer[start : m.end() + 10]
        if "[^" in window or "(doi" in window.lower() or "doi.org" in window.lower():
            continue
        hits.append(m.group(1))
    return hits[:12]
