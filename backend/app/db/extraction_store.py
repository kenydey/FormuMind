"""Persistence for Phase 0 structured-extraction tables.

``extraction_tables`` / ``extraction_formulas`` are filled by layout-aware
parsers (MinerU, Phase 1); the text chain never writes here. Both writers
replace a source's rows atomically, mirroring ``ChunkStore.replace_for_source``
semantics, so a re-ingest cannot leave stale tables behind.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .db_common import safe_bbox
from .models import ExtractionFormula, ExtractionTable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExtractionStore:
    def __init__(self, session_factory) -> None:
        self._factory = session_factory

    def replace_tables(self, source_id: str, items: list[dict]) -> int:
        """Replace all ``extraction_tables`` rows for ``source_id``.

        Each item: {page_no?, bbox?, caption?, markdown_text, n_rows?, n_cols?}.
        """
        with self._factory() as session:
            n = self.replace_tables_in(session, source_id, items)
            session.commit()
            return n

    def replace_tables_in(self, session: Session, source_id: str, items: list[dict]) -> int:
        session.query(ExtractionTable).filter(
            ExtractionTable.source_id == source_id
        ).delete()
        for item in items:
            raw_bbox = item.get("bbox")
            session.add(
                ExtractionTable(
                    id=str(uuid.uuid4()),
                    source_id=source_id,
                    page_no=item.get("page_no"),
                    bbox=safe_bbox(raw_bbox),
                    caption=(item.get("caption") or "")[:500] or None,
                    markdown_text=item.get("markdown_text") or "",
                    n_rows=item.get("n_rows"),
                    n_cols=item.get("n_cols"),
                    created_at=_utcnow(),
                )
            )
        session.flush()
        return len(items)

    def replace_formulas(self, source_id: str, items: list[dict]) -> int:
        """Replace all ``extraction_formulas`` rows for ``source_id``.

        Each item: {page_no?, bbox?, latex, formula_no?}.
        """
        with self._factory() as session:
            n = self.replace_formulas_in(session, source_id, items)
            session.commit()
            return n

    def replace_formulas_in(
        self, session: Session, source_id: str, items: list[dict]
    ) -> int:
        session.query(ExtractionFormula).filter(
            ExtractionFormula.source_id == source_id
        ).delete()
        for item in items:
            raw_bbox = item.get("bbox")
            session.add(
                ExtractionFormula(
                    id=str(uuid.uuid4()),
                    source_id=source_id,
                    page_no=item.get("page_no"),
                    bbox=safe_bbox(raw_bbox),
                    latex=item.get("latex") or "",
                    formula_no=item.get("formula_no"),
                    created_at=_utcnow(),
                )
            )
        session.flush()
        return len(items)

    def tables_for_source(self, source_id: str) -> list[ExtractionTable]:
        with self._factory() as session:
            return (
                session.query(ExtractionTable)
                .filter(ExtractionTable.source_id == source_id)
                .order_by(ExtractionTable.page_no)
                .all()
            )

    def formulas_for_source(self, source_id: str) -> list[ExtractionFormula]:
        with self._factory() as session:
            return (
                session.query(ExtractionFormula)
                .filter(ExtractionFormula.source_id == source_id)
                .order_by(ExtractionFormula.page_no)
                .all()
            )
