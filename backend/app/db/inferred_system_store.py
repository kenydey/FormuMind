"""LLM self-learning formulation-system knowledge base (``inferred_systems``).

Backs P2: unknown product_types get their LLM-inferred constraints persisted
here so later hits reuse them instead of re-inferring. Hot entries (hit_count ≥
threshold) are promotion candidates for the static knowledge base.
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker

from ..domain.schemas import InferredSystem
from .models import InferredSystemRow
from .session_utils import commit_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_system(row: InferredSystemRow) -> InferredSystem:
    return InferredSystem(
        system_name=row.system_name or "",
        must_include_roles=list(row.must_include_roles or []),
        must_exclude=row.must_exclude or "",
        constraints=list(row.constraints or []),
        metric_ranges=dict(row.metric_ranges or {}),
        confidence=row.confidence or "medium",
    )


class InferredSystemStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def match(self, normalized_key: str) -> InferredSystem | None:
        """Return a cached system by normalized key; bump hit_count on hit."""
        with commit_session(self._session_factory) as session:
            row = (
                session.query(InferredSystemRow)
                .filter(InferredSystemRow.normalized_key == normalized_key)
                .filter(InferredSystemRow.status == "active")
                .first()
            )
            if row is None:
                return None
            row.hit_count = (row.hit_count or 0) + 1
            return _row_to_system(row)

    def upsert(
        self,
        normalized_key: str,
        product_type: str,
        system: InferredSystem,
        *,
        source_requirement_id: str | None = None,
        source_requirement_text: str = "",
    ) -> None:
        """Insert or update a cached system (idempotent on normalized_key)."""
        with commit_session(self._session_factory) as session:
            row = (
                session.query(InferredSystemRow)
                .filter(InferredSystemRow.normalized_key == normalized_key)
                .first()
            )
            if row is None:
                row = InferredSystemRow(normalized_key=normalized_key)
                session.add(row)
            row.product_type = product_type
            row.system_name = system.system_name
            row.must_include_roles = list(system.must_include_roles)
            row.must_exclude = system.must_exclude
            row.constraints = list(system.constraints)
            row.metric_ranges = dict(system.metric_ranges)
            row.confidence = system.confidence
            if source_requirement_id:
                row.source_requirement_id = source_requirement_id
            if source_requirement_text:
                row.source_requirement_text = source_requirement_text
            row.updated_at = _utcnow()

    def hot(self, threshold: int = 5) -> list[dict]:
        """Active systems with hit_count ≥ threshold, for promotion review."""
        with self._session_factory() as session:
            rows = (
                session.query(InferredSystemRow)
                .filter(InferredSystemRow.status == "active")
                .filter(InferredSystemRow.hit_count >= threshold)
                .order_by(InferredSystemRow.hit_count.desc())
                .all()
            )
            return [
                {
                    "normalized_key": r.normalized_key,
                    "product_type": r.product_type,
                    "system_name": r.system_name,
                    "hit_count": r.hit_count,
                    "confidence": r.confidence,
                    "source_requirement_text": r.source_requirement_text,
                }
                for r in rows
            ]


_store: InferredSystemStore | None = None


def get_inferred_system_store() -> InferredSystemStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = InferredSystemStore(default_session_factory())
    return _store
