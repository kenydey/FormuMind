"""Federated multi-source search facade for CRAG fallback ingestion."""
from __future__ import annotations

from .errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import time
from typing import Callable

from loguru import logger
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..domain.schemas import Evidence, Requirement
from ..services.deep_research.models import RetrievalHit
from . import literature


class FederatedSearchResult(BaseModel):
    hits: list[RetrievalHit] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    source_counts: dict[str, int] = Field(default_factory=dict)
    query: str = ""
    elapsed_ms: float = 0.0


class FederatedSearchEngine:
    """Wrap ``literature.iter_search`` with typed results and logging."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def effective_sources(self) -> list[str]:
        sources = list(self._settings.federated_sources)
        if self._settings.federated_sources_notebooklm or self._settings.notebooklm_enabled:
            if "notebooklm" not in sources:
                sources.append("notebooklm")
        return sources

    def search(
        self,
        query: str,
        *,
        source_types: list[str] | None = None,
        req: Requirement | None = None,
        total_limit: int | None = None,
        per_source_cap: int | None = None,
        progress_cb: Callable[[list[Evidence]], None] | None = None,
    ) -> FederatedSearchResult:
        types = source_types or self.effective_sources()
        limit = total_limit or min(120, self._settings.search_total_limit)
        cap = per_source_cap or min(30, self._settings.search_limit_per_source)
        t0 = time.perf_counter()

        logger.info("FederatedSearch query={!r} sources=%s", query[:80], types)
        try:
            evidence = literature.iter_search(
                query,
                types,
                req=req,
                total_limit=limit,
                per_source_cap=cap,
                progress_cb=progress_cb,
            )[0]
        except Exception as exc:
            logger.exception("FederatedSearch failed: %s", exc)
            evidence = []

        hits = [RetrievalHit.from_evidence(ev) for ev in evidence]
        counts: dict[str, int] = {}
        for h in hits:
            counts[h.source] = counts.get(h.source, 0) + 1

        elapsed = (time.perf_counter() - t0) * 1000.0
        logger.info(
            "FederatedSearch done hits={} sources={} elapsed_ms={:.0f}",
            len(evidence),
            counts,
            elapsed,
        )
        return FederatedSearchResult(
            hits=hits,
            evidence=evidence,
            source_counts=counts,
            query=query,
            elapsed_ms=elapsed,
        )
