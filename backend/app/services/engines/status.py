"""Lightweight import probes for optional DOE / optimize / parse engines.

Used by ``GET /api/meta`` so the UI can show which engines are actually
installed — ``engine=auto`` otherwise silently falls back with only a warning.
"""
from __future__ import annotations

import logging

from ..errors import log_handled_exception

logger = logging.getLogger(__name__)


def _probe(import_name: str) -> bool:
    try:
        __import__(import_name)
        return True
    except Exception as exc:
        log_handled_exception(logger, exc, f"optional engine probe ({import_name})")
        return False


def engines_status() -> dict[str, dict[str, object]]:
    """Return ``{engine_id: {available, label}}`` for UI / meta consumers."""
    from .doe_registry import baybe_available, pydoe_available
    from ..rag import _embedding_available

    return {
        "baybe": {
            "available": bool(baybe_available()),
            "label": "BayBE 约束贝叶斯 / Campaign",
        },
        "pydoe": {
            "available": bool(pydoe_available()),
            "label": "pyDOE 经典实验设计",
        },
        "optuna": {
            "available": _probe("optuna"),
            "label": "Optuna TPE 寻优",
        },
        "botorch": {
            "available": _probe("botorch") and _probe("gpytorch") and _probe("torch"),
            "label": "BoTorch GP 寻优",
        },
        "summit": {
            "available": _probe("summit"),
            "label": "Summit SOBO 寻优",
        },
        "sentence_transformers": {
            "available": bool(_embedding_available()),
            "label": "句向量嵌入 (FAISS hybrid)",
        },
        "cross_encoder": {
            "available": bool(_cross_encoder_available()),
            "label": "Cross-Encoder 精排 (bge-reranker)",
        },
        "docling": {
            "available": _probe("docling"),
            "label": "Docling PDF 解析",
        },
    }


def _cross_encoder_available() -> bool:
    try:
        from ..rag import cross_encoder_available

        return bool(cross_encoder_available())
    except Exception as exc:
        log_handled_exception(logger, exc, "cross-encoder engine probe")
        return False
