"""LLM-based structured source guide extraction for ingested documents."""
from __future__ import annotations

import logging
import re

from ..config import get_settings
from ..domain.schemas import SourceGuideSchema
from .llm import complete_structured

logger = logging.getLogger(__name__)

SOURCE_GUIDE_SYSTEM = """你是一位严谨的化学与材料学专家。请阅读以下文献，提取关键信息并严格按 JSON Schema 输出。
- summary: 300字内核心机理与工艺摘要（树脂、转化膜、基材等）
- key_entities: 化学物质全称及 CAS 号（格式如 "六氟锆酸 (CAS: 12021-95-3)"）
- parameter_space: 实施例（Examples）中的浓度、温度、时间、pH 等，转为 min_value/max_value/unit；仅文献明确数值；无区间则 min=max
- faqs: 3 个该文献能回答的核心工程问题
绝不允许捏造数据。"""


def _select_extraction_text(full_text: str, *, max_chars: int) -> str:
    """化学专利优先：实施例/Example/配比/浓度 段落 + 文首摘要。"""
    priority_patterns = [
        r"实施例",
        r"Example",
        r"配比",
        r"浓度",
        r"g/L",
        r"wt%",
        r"°C",
        r"pH",
    ]
    paragraphs = [p.strip() for p in full_text.split("\n\n") if p.strip()]
    scored = sorted(
        paragraphs,
        key=lambda p: sum(1 for pat in priority_patterns if re.search(pat, p, re.I)),
        reverse=True,
    )
    excerpt: list[str] = []
    size = 0
    for p in scored:
        if size + len(p) > max_chars:
            break
        excerpt.append(p)
        size += len(p)
    return "\n\n".join(excerpt) if excerpt else full_text[:max_chars]


def extract_source_guide(text: str, *, title: str = "") -> tuple[SourceGuideSchema | None, str | None]:
    try:
        settings = get_settings()
        excerpt = _select_extraction_text(text, max_chars=settings.source_guide_max_chars)
        guide, err = complete_structured(
            SOURCE_GUIDE_SYSTEM,
            f"Title: {title}\n\nDocument:\n{excerpt}",
            SourceGuideSchema,
            retry=True,
        )
        return (guide, None) if guide else (None, err or "empty")
    except Exception as exc:
        logger.warning("source_guide extraction failed: %s", exc)
        return None, str(exc)
