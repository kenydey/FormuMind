"""Evidence Synthesis mode — PaperQA2 + hybrid_strict + AIPOCH discipline."""
from __future__ import annotations

import logging
from typing import Any

from ..domain.schemas import Evidence

logger = logging.getLogger(__name__)

LITERATURE_DISCIPLINE = """
# Evidence Synthesis discipline (mandatory)

1. Retrieve-first: only assert what the provided sources support.
2. Every non-trivial claim needs a citation to a provided source (`[^n]` or clear title/id).
3. Do not invent DOIs, page numbers, or numeric process windows.
4. Synthesis = comparison by theme, not a bullet bibliography.
5. When evidence is missing, write **无据 / unavailable** explicitly — never invent.
6. Calibrate confidence: preprint / single study / contested vs established.
7. End with a short 「证据边界」 section.
""".strip()


def evidence_mode_active(mode: str | None, settings: Any) -> bool:
    raw = (mode or "").strip().lower()
    if raw in ("evidence", "paperqa", "hybrid_strict"):
        return True
    flag = getattr(settings, "evidence_synthesis_mode", "off") or "off"
    return str(flag).strip().lower() not in ("", "off", "false", "0")


def build_evidence_prompt_prefix(
    *,
    skill_ids: list[str] | None = None,
    settings: Any = None,
) -> str:
    parts = [LITERATURE_DISCIPLINE]
    try:
        from .chat_skills import skill_prompt_block

        block = skill_prompt_block(list(skill_ids or []))
        if block:
            parts.append(block)
        elif evidence_mode_active("evidence", settings):
            # Default skill when evidence mode on but none selected
            from .skills_store import is_enabled

            if is_enabled("literature-review"):
                block = skill_prompt_block(["literature-review"])
                if block:
                    parts.append(block)
    except Exception as exc:  # noqa: BLE001
        logger.debug("skill inject skipped: %s", exc)
    return "\n\n".join(parts)


def try_paperqa_answer(
    question: str,
    sources: list[Evidence],
) -> tuple[str, list[Evidence]] | None:
    """Async-safe PaperQA attempt for stream/sync callers."""
    try:
        from .llm import _paperqa_available, _paperqa_answer
        import asyncio

        if not _paperqa_available() or not sources:
            return None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_paperqa_answer(question, sources))
        # Running loop: schedule via new loop in thread is handled by caller
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("paperqa evidence skipped: %s", exc)
        return None


async def try_paperqa_answer_async(
    question: str,
    sources: list[Evidence],
) -> tuple[str, list[Evidence]] | None:
    try:
        from .llm import _paperqa_available, _paperqa_answer

        if not _paperqa_available() or not sources:
            return None
        return await _paperqa_answer(question, sources)
    except Exception as exc:  # noqa: BLE001
        logger.debug("paperqa async skipped: %s", exc)
        return None


def enrich_chat_prompt(
    base_prompt: str,
    *,
    mode: str | None,
    skill_ids: list[str] | None,
    settings: Any,
) -> str:
    if not evidence_mode_active(mode, settings) and not skill_ids:
        return base_prompt
    prefix = build_evidence_prompt_prefix(skill_ids=skill_ids, settings=settings)
    if not prefix:
        return base_prompt
    return f"{prefix}\n\n---\n\n{base_prompt}"


def postprocess_evidence_answer(
    answer: str,
    *,
    settings: Any,
) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {"doi_results": [], "reviewer": None}
    doi_on = bool(getattr(settings, "evidence_doi_verify_enabled", True))
    if doi_on:
        from .scholar_helpers import annotate_answer_dois

        answer, doi_results = annotate_answer_dois(answer, enabled=True)
        meta["doi_results"] = doi_results
    return answer, meta
