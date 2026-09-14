"""Optional LLM narrative for project dossier sections (P4.3).

Flag-gated via ``wiki_dossier_llm_narrative`` (default false). Never rewrites
deterministic markdown tables; never invents image paths. On failure, callers
keep the previous narrative.

When enabled, generation uses a **two-step** chain (idea borrowed from
Karpathy-style wikis; reimplemented here):
  1) structured analysis JSON (entities / tensions / soft next steps)
  2) Chinese prose from that analysis + the canonical table only
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from ...config import get_settings
from .vertical_addendum import resolve_addendum

logger = logging.getLogger(__name__)

_TABLE_ROW = re.compile(r"^\s*\|.+\|\s*$", re.MULTILINE)
_IMAGE_MD = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_FAKE_ASSET = re.compile(r"(?:images|artifacts)/[A-Za-z0-9._\-/]+\.(?:png|jpg|jpeg|gif|webp|svg)", re.I)

_SECTION_PROMPTS: dict[str, str] = {
    "S1_requirements": "根据技术要求表，用中文写 2–4 句问题背景与必须满足的工艺窗口（定性为主）。",
    "S2_literature": "根据文献/来源表，概括机理共识与争议；不要编造未列出的文献。",
    "S3_baseline_formula": "根据基准配方表，简述物料角色与配比逻辑；单位以表为准。",
    "S4_doe": "根据 DOE 设计/试验结果表，说明因子选择与试验覆盖；禁止编造未入库 run。",
    "S5_lab_ledger": "根据实验台账表，概括测量覆盖与明显缺口。",
    "S6_optimize_loop": "根据闭环/候选表，概述收敛态势与下一步软建议（非硬边界）。",
    "S7_artifacts": "根据资产表描述已有图表/多模态资产；若表空则说明暂无持久化资产。",
    "S8_open_questions": "把 Flag/缺口整理为简洁的下一步清单。",
}


def narrative_enabled() -> bool:
    settings = get_settings()
    return bool(
        settings.wiki_enabled
        and getattr(settings, "wiki_project_dossier_enabled", False)
        and getattr(settings, "wiki_dossier_llm_narrative", False)
    )


def validate_narrative(text: str, *, table_md: str = "") -> str | None:
    """Return error reason if narrative is unsafe; None if ok."""
    body = (text or "").strip()
    if not body:
        return "empty"
    if _TABLE_ROW.search(body):
        return "contains_table_row"
    if _IMAGE_MD.search(body) or _FAKE_ASSET.search(body):
        return "invented_asset_path"
    if "<!-- data:" in body:
        return "contains_data_marker"
    return None


def extract_narrative_from_section(section_body: str) -> str:
    """Return text after the first markdown table block (narrative region)."""
    text = (section_body or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith("<!--")):
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("|"):
        while i < len(lines) and (lines[i].lstrip().startswith("|") or not lines[i].strip()):
            i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and lines[i].lstrip().startswith("|"):
            while i < len(lines) and (lines[i].lstrip().startswith("|") or not lines[i].strip()):
                i += 1
    return "\n".join(lines[i:]).strip()


def attach_narrative(table_block: str, narrative: str | None) -> str:
    """Compose section body = table block + narrative (or keep placeholder)."""
    base = (table_block or "").rstrip()
    narr = (narrative or "").strip()
    if not narr:
        return base
    return f"{base}\n\n{narr}"


def _analysis_brief(analysis: Any) -> str:
    """Compact analysis for step-2 prompt (no tables)."""
    if analysis is None:
        return ""
    if hasattr(analysis, "model_dump"):
        data = analysis.model_dump()
    elif isinstance(analysis, dict):
        data = analysis
    else:
        return str(analysis)[:2000]
    return json.dumps(data, ensure_ascii=False)[:2500]


def generate_section_narrative(
    section: str,
    *,
    table_md: str,
    pack: dict[str, Any],
    previous_narrative: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Best-effort two-step LLM narrative for one section.

    Returns ``(narrative_or_previous, meta)``. Meta includes model / prompt_hash /
    ``used_llm`` / ``error`` / ``steps``.
    """
    meta: dict[str, Any] = {
        "used_llm": False,
        "model": "",
        "prompt_hash": "",
        "error": None,
        "section": section,
        "steps": [],
    }
    prev = (previous_narrative or "").strip()
    if not narrative_enabled():
        meta["error"] = "narrative_flag_off"
        return prev, meta

    prompt_hint = _SECTION_PROMPTS.get(section, "用中文写简短技术叙述，不得改表内数字。")
    addendum = resolve_addendum(pack.get("vertical_addendum") or "")
    title = pack.get("title") or pack.get("project_id") or ""
    domain = pack.get("domain") or ""

    try:
        from pydantic import BaseModel, Field

        from ..llm import complete_structured

        class SectionAnalysis(BaseModel):
            key_points: list[str] = Field(
                default_factory=list,
                description="3-6 qualitative bullets grounded only in the table",
            )
            tensions: list[str] = Field(
                default_factory=list,
                description="Contradictions / gaps visible in the table; empty if none",
            )
            soft_next_steps: list[str] = Field(
                default_factory=list,
                description="Soft suggestions only; never hard DOE bounds or fake cites",
            )

        class SectionNarrative(BaseModel):
            narrative: str = Field(
                description="Chinese prose only; no markdown tables; no image paths"
            )

        base_system = (
            "You assist FormuMind Project Dossier (industrial R&D wiki). "
            "Use ONLY the provided markdown table / pack facts. "
            "Do NOT invent numbers, citations, ASTM results, or image/asset paths. "
            "Do NOT propose hard DOE bounds. Soft suggestions only."
        )
        if addendum:
            base_system += "\n\n" + addendum

        table_blob = table_md[:4000] or "(empty)"
        analysis_user = (
            f"Project: {title}\nDomain: {domain}\nSection: {section}\n"
            f"Task: Analyze the canonical table only. Output structured bullets.\n\n"
            f"Canonical table:\n{table_blob}\n"
        )
        prompt_hash = hashlib.sha1(f"{base_system}\n{analysis_user}\n{prompt_hint}".encode()).hexdigest()[
            :16
        ]
        meta["prompt_hash"] = prompt_hash
        meta["model"] = getattr(get_settings(), "llm_model", "") or ""

        # Step 1 — analysis
        analysis, err1 = complete_structured(
            base_system + " Reply with structured analysis fields only.",
            analysis_user,
            SectionAnalysis,
            retry=False,
        )
        if analysis is None:
            meta["error"] = err1 or "analysis_llm_failed"
            meta["steps"] = ["analysis_failed"]
            return prev, meta
        meta["steps"].append("analysis_ok")

        # Step 2 — prose from analysis + table
        prose_system = (
            base_system
            + " Write short Chinese narrative only. "
            "Do NOT output markdown tables. Do NOT use ![ ]( ) images. "
            "Keep claims qualitative unless the table states a number."
        )
        prose_user = (
            f"Project: {title}\nDomain: {domain}\nSection: {section}\n"
            f"Instruction: {prompt_hint}\n\n"
            f"Structured analysis (from step 1):\n{_analysis_brief(analysis)}\n\n"
            f"Canonical table:\n{table_blob}\n"
        )
        out, err2 = complete_structured(prose_system, prose_user, SectionNarrative, retry=False)
        if out is None:
            meta["error"] = err2 or "prose_llm_failed"
            meta["steps"].append("prose_failed")
            return prev, meta

        text = (out.narrative or "").strip()
        bad = validate_narrative(text, table_md=table_md)
        if bad:
            meta["error"] = f"validation:{bad}"
            meta["steps"].append("validation_failed")
            return prev, meta

        meta["used_llm"] = True
        meta["steps"].append("prose_ok")
        meta["analysis"] = {
            "key_points": list(getattr(analysis, "key_points", None) or []),
            "tensions": list(getattr(analysis, "tensions", None) or []),
            "soft_next_steps": list(getattr(analysis, "soft_next_steps", None) or []),
        }
        return text, meta
    except Exception as exc:  # noqa: BLE001
        logger.info("dossier narrative unavailable section=%s: %s", section, exc)
        meta["error"] = str(exc)
        return prev, meta
