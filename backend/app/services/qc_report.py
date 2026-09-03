"""Extract typed measurements from a QC / test report.

A lab certificate carries what a bare number cannot: the standard the test was
run under, the instrument, the acceptance window, and the verdict. Those are
exactly the fields that make a result comparable and auditable, and they were
being discarded — QC reports landed in the knowledge base as generic corpus
text with no path back to the experiment they measured.

Follows the ``source_guide`` house pattern: a Chinese system prompt with a
per-field spec and a fabrication ban, a relevance-scored excerpt so long
reports fit the token budget, ``complete_structured`` with retry, and a
degraded sentinel rather than an exception on failure.
"""
from __future__ import annotations

import logging
import re

from ..config import get_settings
from ..domain.schemas import Measurement
from .llm import complete_structured
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

QC_REPORT_SYSTEM = """你是一位严谨的材料检测工程师。请阅读以下检测报告，提取每一项检测结果，严格按 JSON Schema 输出。
- metric: 检测项目的机器键名，用小写英文下划线式。常见映射：
  耐盐雾/中性盐雾 → salt_spray_hours；附着力 → adhesion_mpa；铅笔硬度 → pencil_hardness_idx；
  膜厚/干膜重 → film_weight_gsm；VOC → voc_gpl；清洗率/除油率 → cleaning_efficiency；
  膜重 → coating_weight_gsm。无法映射时用报告原文的英文化下划线名。
- value: 纯数字。带区间（如 ">=720"、"720~800"）取实测代表值；无数字则跳过该项。
- unit: 原文单位（h、MPa、g/L、g/m²、% 等）。
- test_method: 检测依据标准全称，如 "ASTM B117"、"ISO 9227"、"GB/T 1771"、"GB/T 5210"。
  这是硬性要求：没有方法的耐盐雾数值不可比较。原文未写则留空字符串。
- instrument: 检测仪器型号；operator: 检测人；均按原文，未写则空字符串。
- spec_min / spec_max: 技术要求/验收指标的下限与上限，仅当报告明确给出；只有单边则另一边为 null。
- note: 该项的备注或试验条件（如 "5% NaCl, 35°C"）。
只提取报告中明确记载的检测项，绝不允许捏造数据或推断未写明的标准。"""

# Sections of a certificate that actually carry results.
_PRIORITY_PATTERNS = [
    r"检测项目", r"检验项目", r"试验项目", r"测试结果", r"检测结果", r"实测值",
    r"技术要求", r"判定", r"合格", r"不合格", r"单项判定",
    r"salt\s*spray", r"adhesion", r"ASTM", r"ISO\s*\d", r"GB/T",
    r"\bh\b", r"MPa", r"g/L", r"g/m",
]
_TABLE_PATTERNS = [r"\|[ \-:]+\|", r"^\s*\|.+\|", r"检测项目.*结果", r"项目.*技术要求"]


class QCMeasurement(BaseModel):
    """One row of a test report.

    ``value`` is a plain float, not a union with str: this feeds
    ``ExperimentRecord`` and the training pipeline, and a string here would be
    silently dropped downstream rather than raising.
    """

    metric: str = Field(min_length=1)
    value: float
    unit: str = ""
    test_method: str = ""
    instrument: str = ""
    operator: str = ""
    spec_min: float | None = None
    spec_max: float | None = None
    note: str = ""


class QCReportExtraction(BaseModel):
    report_id: str = ""
    sample_id: str = ""
    tested_at: str = ""
    laboratory: str = ""
    measurements: list[QCMeasurement] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    notes: str = ""

    @classmethod
    def degraded(cls, note: str = "") -> QCReportExtraction:
        return cls(confidence=0.0, notes=note[:200])

    def to_measurements(self, *, source_document_id: str | None = None) -> list[Measurement]:
        """Convert to the domain type. ``passed`` derives from the spec window."""
        return [
            Measurement(
                metric=m.metric.strip(),
                value=m.value,
                unit=m.unit,
                test_method=m.test_method,
                instrument=m.instrument,
                operator=m.operator,
                spec_min=m.spec_min,
                spec_max=m.spec_max,
                source_document_id=source_document_id,
                note=m.note,
            )
            for m in self.measurements
            if m.metric.strip()
        ]


def _score_paragraph(para: str) -> int:
    score = sum(1 for pat in _PRIORITY_PATTERNS if re.search(pat, para, re.I))
    score += sum(2 for pat in _TABLE_PATTERNS if re.search(pat, para, re.I | re.M))
    return score


def _select_extraction_text(full_text: str, *, max_chars: int) -> str:
    """Result tables and spec rows first, so a long report still fits."""
    paragraphs = [p.strip() for p in full_text.split("\n\n") if p.strip()]
    scored = sorted(paragraphs, key=_score_paragraph, reverse=True)
    excerpt: list[str] = []
    size = 0
    for para in scored:
        if size + len(para) > max_chars:
            break
        excerpt.append(para)
        size += len(para)
    return "\n\n".join(excerpt) if excerpt else full_text[:max_chars]


def extract_qc_report(
    text: str, *, title: str = ""
) -> tuple[QCReportExtraction, str | None]:
    """Parse a report into typed measurements.

    Always returns an extraction — a degraded one when no LLM is configured or
    the call fails — so the caller can still store and bind the document.
    """
    if not (text or "").strip():
        return QCReportExtraction.degraded("空文档"), "empty document"
    try:
        settings = get_settings()
        excerpt = _select_extraction_text(text, max_chars=settings.source_guide_max_chars)
        extraction, err = complete_structured(
            QC_REPORT_SYSTEM,
            f"报告名称：{title}\n\n报告内容：\n{excerpt}",
            QCReportExtraction,
            retry=True,
        )
        if extraction:
            return extraction, None
        return QCReportExtraction.degraded(err or "empty"), err or "empty"
    except Exception as exc:
        logger.warning("qc report extraction failed: %s", exc)
        return QCReportExtraction.degraded(str(exc)[:200]), str(exc)
