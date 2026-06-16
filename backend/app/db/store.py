"""Experiment persistence stores.

Two interchangeable backends implement the same small interface so the
``ModelRegistry`` is agnostic to where data lives:

* :class:`SqlExperimentStore` — SQLAlchemy-backed (SQLite by default), the
  production default. Transactional, queryable, and concurrency-safe.
* :class:`JsonExperimentStore` — the original single-file JSON store, retained
  for lightweight tests and as the legacy migration source.

Interface: ``all() -> list[ExperimentRecord]``, ``add(records)``, ``clear()``.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.schemas import ExperimentRecord
from .models import ExperimentRow


class ExperimentStore(Protocol):
    def all(self) -> list[ExperimentRecord]: ...
    def add(self, records: list[ExperimentRecord]) -> None: ...
    def clear(self) -> None: ...


class JsonExperimentStore:
    """Single-file JSON persistence (the original v0.1 store)."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def all(self) -> list[ExperimentRecord]:
        with self._lock:
            if not self.path.exists():
                return []
            raw = json.loads(self.path.read_text() or "[]")
            return [ExperimentRecord(**r) for r in raw]

    def _write(self, records: list[ExperimentRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([r.model_dump() for r in records], indent=2))

    def add(self, records: list[ExperimentRecord]) -> None:
        with self._lock:
            current = self.all()
            current.extend(records)
            self._write(current)

    def clear(self) -> None:
        with self._lock:
            self._write([])


def _row_to_record(row: ExperimentRow) -> ExperimentRecord:
    return ExperimentRecord(
        domain=row.domain,
        factors=row.factors or {},
        cure_temperature_c=row.cure_temperature_c,
        measured=row.measured,
        source=row.source,
        label=row.label,
    )


def _record_to_row(rec: ExperimentRecord) -> ExperimentRow:
    return ExperimentRow(
        domain=rec.domain.value,
        factors=rec.factors,
        cure_temperature_c=rec.cure_temperature_c,
        measured=rec.measured,
        source=rec.source,
        label=rec.label,
    )


class SqlExperimentStore:
    """SQLAlchemy-backed experiment store (SQLite default, Postgres-ready)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        # Serialises writes so SQLite never loses a row under thread contention.
        self._write_lock = threading.Lock()

    def all(self) -> list[ExperimentRecord]:
        with self._session_factory() as session:
            rows = session.scalars(select(ExperimentRow).order_by(ExperimentRow.id)).all()
            return [_row_to_record(r) for r in rows]

    def add(self, records: list[ExperimentRecord]) -> None:
        if not records:
            return
        with self._write_lock, self._session_factory() as session:
            session.add_all([_record_to_row(r) for r in records])
            session.commit()

    def clear(self) -> None:
        with self._write_lock, self._session_factory() as session:
            session.execute(delete(ExperimentRow))
            session.commit()

    def count(self) -> int:
        with self._session_factory() as session:
            return len(session.scalars(select(ExperimentRow.id)).all())
