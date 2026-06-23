"""Knowledge-cohort deep research — deprecated alias for DeepResearchEngine.

Prefer ``from app.services.deep_research import DeepResearchEngine`` for new code.
"""
from __future__ import annotations

import warnings
from typing import Callable

from ..domain.schemas import ComprehensiveReport, Evidence, Requirement
from .deep_research.engine import DeepResearchEngine


class KnowledgeCohort:
    """Deprecated: use :class:`DeepResearchEngine` instead."""

    def run(
        self,
        topic: str,
        req: Requirement | None = None,
        source_types: list[str] | None = None,
        progress_cb: Callable[[float, str], None] | None = None,
        retrieval_progress_cb: Callable[[list[Evidence]], None] | None = None,
    ) -> ComprehensiveReport:
        warnings.warn(
            "KnowledgeCohort is deprecated; use DeepResearchEngine.run()",
            DeprecationWarning,
            stacklevel=2,
        )
        return DeepResearchEngine().run(
            topic,
            req=req,
            source_types=source_types,
            progress_cb=progress_cb,
            retrieval_progress_cb=retrieval_progress_cb,
        )


def conduct_research(
    topic: str,
    req: Requirement | None = None,
    source_types: list[str] | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    retrieval_progress_cb: Callable[[list[Evidence]], None] | None = None,
) -> ComprehensiveReport:
    """Module-level entry point for the task layer."""
    return DeepResearchEngine().run(
        topic,
        req=req,
        source_types=source_types,
        progress_cb=progress_cb,
        retrieval_progress_cb=retrieval_progress_cb,
    )
