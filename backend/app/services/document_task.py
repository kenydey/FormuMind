"""DocumentTask — unified ingestion entry for a DOI / arXiv id / patent / URL.

2026-09-05 (plan 2026-09-05-multi-source-ingestion-audit.md, P1): 把"给一个
identifier 就完整走一遍全文获取 → 入库"收敛为单入口, 内部纯复用现有
fulltext_fetcher(源码/PDF/HTML 三梯度)与 ingestion._ingest_parsed_text(切块
入库/幂等/双语标注), 不新引依赖。

doc_type ∈ {"patent", "paper", "web"}:
  - patent: 专利号(如 CN104561970A / US20130052352A1) → Google Patents 语义
    DOM 全文(免 OCR)或 citation PDF
  - paper : DOI / arXiv id → arXiv LaTeX 源码 → OA PDF 多镜像 → landing HTML
  - web   : 普通 URL → 现有 ingest_url(trafilatura/parsing 级联)
"""
from __future__ import annotations

import logging
import types
from dataclasses import dataclass, field

from ..config import get_settings

logger = logging.getLogger(__name__)

_DOC_TYPES = {"patent", "paper", "web"}


@dataclass
class TaskOutcome:
    doc_type: str
    identifier: str
    text_chars: int = 0
    tier: str = ""  # "source" | "pdf" | "html" | "web"
    error: str | None = None
    evidence: list = field(default_factory=list)
    source_id: str | None = None
    source_guide: object | None = None
    extraction_status: str = ""


def _evidence_like(identifier: str):
    """Minimal Evidence stand-in consumed by fulltext_fetcher internals."""
    return types.SimpleNamespace(
        identifier=identifier,
        is_oa=None,
        oa_pdf_url=None,
        is_seed_corpus=False,
    )


def resolve_document(doc_type: str, identifier: str, *, timeout: float | None = None) -> TaskOutcome:
    """Fetch full text for an identifier and route it into the knowledge base."""
    doc_type = (doc_type or "").strip().lower()
    identifier = (identifier or "").strip()
    if doc_type not in _DOC_TYPES:
        raise ValueError(f"doc_type 必须是 {sorted(_DOC_TYPES)} 之一, 收到 {doc_type!r}")
    if not identifier:
        raise ValueError("identifier 不能为空")
    timeout = timeout or getattr(get_settings(), "ingest_fetch_timeout", 30.0)

    from . import fulltext_fetcher
    from .ingestion import IngestOutcome, ingest_text, ingest_url

    try:
        if doc_type == "web":
            if not identifier.lower().startswith(("http://", "https://")):
                raise ValueError("web 类型 identifier 必须是 http(s) URL")
            outcome = ingest_url(identifier, persist=True)
            return _from_ingest("web", identifier, outcome, tier="web")

        text: str | None = None
        tier = ""
        assert timeout is not None
        if doc_type == "patent":
            from .fulltext_fetcher import _fetch_patent_text

            text = _fetch_patent_text(_evidence_like(identifier.upper()), timeout=timeout)
            tier = "dom"
        else:  # paper
            from .fulltext_fetcher import _fetch_literature_text

            text = _fetch_literature_text(_evidence_like(identifier), timeout=timeout)
            tier = "full"

        if not text or not text.strip():
            raise ValueError("无法获取该标识符的全文(非 OA/被源站拒绝/解析为空)")

        outcome = ingest_text(text, title=identifier, persist=True)
        if not isinstance(outcome, IngestOutcome):
            # 兼容调用方把失败编码为 evidence-only 返回
            if outcome.evidence and getattr(outcome.evidence[0], "snippet", "").startswith("无法"):
                raise ValueError("无法从该标识符提取文本")
        return _from_ingest(doc_type, identifier, outcome, tier=tier, text_chars=len(text))
    except ValueError:
        raise
    except Exception as exc:
        logger.exception("resolve_document failed: %s %s", doc_type, identifier)
        return TaskOutcome(doc_type=doc_type, identifier=identifier, error=str(exc))


def _from_ingest(doc_type: str, identifier: str, outcome, *, tier: str, text_chars: int = 0) -> TaskOutcome:
    evidence = list(getattr(outcome, "evidence", []) or [])
    text_chars = text_chars or sum(len(getattr(ev, "snippet", "") or "") for ev in evidence)
    return TaskOutcome(
        doc_type=doc_type,
        identifier=identifier,
        text_chars=text_chars,
        tier=tier,
        evidence=evidence,
        source_id=getattr(outcome, "source_id", None),
        source_guide=getattr(outcome, "source_guide", None),
        extraction_status=getattr(outcome, "extraction_status", "") or "",
    )
