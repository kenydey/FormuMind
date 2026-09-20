"""Backward-compatible re-export of the shared golden retrieval dataset."""

from __future__ import annotations

from app.resources.golden_retrieval import golden_questions, sample_documents

__all__ = ["golden_questions", "sample_documents"]
