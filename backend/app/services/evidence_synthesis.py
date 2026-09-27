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


def _project_instructions_block(project_id: str | None) -> str:
    """读取项目级 agent_context 并拼成 prompt 块(W1-3 P0-5; fail-open)。

    - project_id 为空 / store 异常 / context 为空 → 返回 ""(不注入)。
    - 防御性读取: 任何异常只记 debug, 不中断主流程。
    """
    if not project_id:
        return ""
    try:
        from ..db.project_store import get_project_store

        detail = get_project_store().get(project_id)
        ctx = ""
        if detail is not None:
            ctx = (getattr(detail.workspace, "agent_context", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("project agent_context inject skipped: %s", exc)
        return ""
    if not ctx:
        return ""
    return "## Project instructions（项目级，不可被 skill 覆盖）\n" + ctx


def build_evidence_prompt_prefix(
    *,
    skill_ids: list[str] | None = None,
    mcp_server_ids: list[str] | None = None,
    settings: Any = None,
    project_id: str | None = None,
) -> str:
    parts = [LITERATURE_DISCIPLINE]
    # W1-3: 项目级指示放在 discipline 之后、skill block 之前。
    project_block = _project_instructions_block(project_id)
    if project_block:
        parts.append(project_block)
    try:
        from .chat_skills import skill_prompt_block

        ids = list(skill_ids or [])
        if mcp_server_ids:
            try:
                from .mcp_skill_docs import ensure_mcp_skill_docs, mcp_skill_ids

                ensure_mcp_skill_docs(
                    server_ids=list(mcp_server_ids),
                    settings=settings,
                    probe=True,
                )
                ids.extend(mcp_skill_ids(list(mcp_server_ids)))
            except Exception as exc:  # noqa: BLE001
                logger.debug("mcp skill-doc inject skipped: %s", exc)
        block = skill_prompt_block(ids)
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
        from .paperqa_engine import answer_with_paperqa, paperqa_available

        if not paperqa_available() or not sources:
            return None
        return answer_with_paperqa(question, sources)
    except Exception as exc:  # noqa: BLE001
        logger.debug("paperqa evidence skipped: %s", exc)
        return None


async def try_paperqa_answer_async(
    question: str,
    sources: list[Evidence],
) -> tuple[str, list[Evidence]] | None:
    try:
        from .paperqa_engine import answer_with_paperqa_async, paperqa_available

        if not paperqa_available() or not sources:
            return None
        return await answer_with_paperqa_async(question, sources)
    except Exception as exc:  # noqa: BLE001
        logger.debug("paperqa async skipped: %s", exc)
        return None


def enrich_chat_prompt(
    base_prompt: str,
    *,
    mode: str | None,
    skill_ids: list[str] | None,
    settings: Any,
    mcp_server_ids: list[str] | None = None,
    project_id: str | None = None,
) -> str:
    # W1-3: 项目级指示在非 evidence 模式下也应生效(早退前先拼接)。
    project_block = _project_instructions_block(project_id)
    # W2-4 (P1-1): agent memory recall — project scope, then global fallback.
    # build_memory_block is fail-open and returns "" when disabled/empty.
    try:
        from .agent_memory import build_memory_block

        mem_block = build_memory_block("project", project_id, base_prompt[:2000])
        if not mem_block:
            mem_block = build_memory_block("global", None, base_prompt[:2000])
    except Exception:
        mem_block = ""
    if (
        not evidence_mode_active(mode, settings)
        and not skill_ids
        and not mcp_server_ids
    ):
        head = "\n\n---\n\n".join(b for b in (project_block, mem_block) if b)
        if head:
            return f"{head}\n\n---\n\n{base_prompt}"
        return base_prompt
    prefix = build_evidence_prompt_prefix(
        skill_ids=skill_ids,
        mcp_server_ids=mcp_server_ids,
        settings=settings,
        project_id=project_id,
    )
    if mem_block:
        prefix = f"{prefix}\n\n{mem_block}" if prefix else mem_block
    if not prefix:
        return base_prompt
    return f"{prefix}\n\n---\n\n{base_prompt}"


def postprocess_evidence_answer(
    answer: str,
    *,
    settings: Any,
) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "doi_results": [],
        "reviewer": None,
        "citation_expand": [],
    }
    doi_on = bool(getattr(settings, "evidence_doi_verify_enabled", True))
    if doi_on:
        from .scholar_helpers import annotate_answer_dois, expand_top_dois, extract_dois

        answer, doi_results = annotate_answer_dois(answer, enabled=True)
        meta["doi_results"] = doi_results
        if bool(getattr(settings, "citation_expand_enabled", True)):
            seeds = [r["doi"] for r in doi_results if r.get("status") == "ok"] or extract_dois(
                answer
            )
            meta["citation_expand"] = expand_top_dois(
                seeds,
                max_seeds=3,
                n_backward=12,
                n_forward=8,
                enabled=True,
            )
    return answer, meta
