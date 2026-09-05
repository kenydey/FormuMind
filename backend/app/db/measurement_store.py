"""Typed measurement rows and experiment↔document bindings.

The experiment's ``measured`` JSON column stays the flat mirror the training
pipeline reads. This store holds the qualified version — unit, test method,
instrument, operator, acceptance window — and the link back to the QC report a
value was read out of.

Both write paths expose an ``*_in(session, ...)`` variant so a caller can bind a
report, its measurements, and the experiment row in one transaction; see
``services/ingest_tx.ingest_document_tx`` for the pattern.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ..domain.schemas import Measurement
from ..services.errors import degrade_return
from .models import ExperimentAttachment, MeasurementRow
from .session_utils import commit_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def row_to_measurement(row: MeasurementRow) -> Measurement:
    return Measurement(
        metric=row.metric,
        value=row.value,
        unit=row.unit or "",
        test_method=row.test_method or "",
        instrument=row.instrument or "",
        operator=row.operator or "",
        measured_at=row.measured_at,
        spec_min=row.spec_min,
        spec_max=row.spec_max,
        passed=row.passed,
        source_document_id=row.source_document_id,
        note=row.note or "",
    )


class MeasurementStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # ── writes ─────────────────────────────────────────────────────────────

    def add_in(
        self,
        session: Session,
        experiment_id: int,
        measurements: list[Measurement],
        *,
        source_document_id: str | None = None,
    ) -> int:
        """Insert measurement rows on a caller-owned session, without committing.

        Flushed before returning so a bad ``experiment_id`` surfaces as an
        IntegrityError here rather than at the caller's commit.
        """
        for measurement in measurements:
            session.add(
                MeasurementRow(
                    id=str(uuid.uuid4()),
                    experiment_id=experiment_id,
                    metric=measurement.metric[:80],
                    value=float(measurement.value),
                    unit=(measurement.unit or "")[:32],
                    test_method=(measurement.test_method or "")[:80],
                    instrument=(measurement.instrument or "")[:120],
                    operator=(measurement.operator or "")[:80],
                    measured_at=measurement.measured_at,
                    spec_min=measurement.spec_min,
                    spec_max=measurement.spec_max,
                    passed=measurement.passed,
                    source_document_id=measurement.source_document_id or source_document_id,
                    note=measurement.note or "",
                    created_at=_utcnow(),
                )
            )
        session.flush()
        return len(measurements)

    def add(
        self,
        experiment_id: int,
        measurements: list[Measurement],
        *,
        source_document_id: str | None = None,
    ) -> int:
        try:
            with commit_session(self._session_factory) as session:
                return self.add_in(
                    session,
                    experiment_id,
                    measurements,
                    source_document_id=source_document_id,
                )
        except Exception as exc:
            return degrade_return(
                logger, exc, f"measurement write failed for experiment {experiment_id}", 0
            )

    def attach_in(
        self,
        session: Session,
        experiment_id: int,
        source_document_id: str,
        *,
        kind: str = "qc_report",
        note: str = "",
    ) -> str | None:
        """Bind a document to an experiment. Returns None when already bound.

        Idempotent via ``uq_experiment_attachment``: a re-upload of the same
        report is not a second attachment.
        """
        savepoint = session.begin_nested()
        attachment_id = str(uuid.uuid4())
        try:
            session.add(
                ExperimentAttachment(
                    id=attachment_id,
                    experiment_id=experiment_id,
                    source_document_id=source_document_id,
                    kind=kind[:32],
                    note=note,
                    created_at=_utcnow(),
                )
            )
            session.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            return None
        except OperationalError:
            raise
        return attachment_id

    def attach(
        self,
        experiment_id: int,
        source_document_id: str,
        *,
        kind: str = "qc_report",
        note: str = "",
    ) -> str | None:
        with commit_session(self._session_factory) as session:
            return self.attach_in(
                session, experiment_id, source_document_id, kind=kind, note=note
            )

    # ── reads ──────────────────────────────────────────────────────────────

    def for_experiment(self, experiment_id: int) -> list[Measurement]:
        with self._session_factory() as session:
            rows = (
                session.query(MeasurementRow)
                .filter(MeasurementRow.experiment_id == experiment_id)
                .order_by(MeasurementRow.metric, MeasurementRow.created_at)
                .all()
            )
            return [row_to_measurement(r) for r in rows]

    def attachments_for(self, experiment_id: int) -> list[ExperimentAttachment]:
        with self._session_factory() as session:
            return (
                session.query(ExperimentAttachment)
                .filter(ExperimentAttachment.experiment_id == experiment_id)
                .order_by(ExperimentAttachment.created_at)
                .all()
            )

    def delete_attachment(self, attachment_id: str) -> bool:
        """P4: unbind an attachment (local row only).

        The DataLab ELN copy (if any) is intentionally kept — the platform's
        delete-file API is inconsistent with its storage layout, so platform
        removal is out of scope (v2 after fixing the platform side).
        """
        try:
            with commit_session(self._session_factory) as session:
                row = session.get(ExperimentAttachment, attachment_id)
                if row is None:
                    return False
                session.delete(row)
                return True
        except Exception as exc:
            logger.warning("delete_attachment({}) failed: {}", attachment_id, exc)
            return False

    def set_attachment_datalab_ref(
        self, experiment_id: int, source_document_id: str, datalab_file_id: str
    ) -> None:
        """Record the Datalab ELN file id on an attachment's note.

        The QC-report pipeline stores the report text in sqlite and mirrors the
        raw bytes into Datalab; this records the ELN copy's id so the two stay
        traceable. Best-effort: a missing attachment is silently ignored.
        """
        try:
            with commit_session(self._session_factory) as session:
                row = (
                    session.query(ExperimentAttachment)
                    .filter(
                        ExperimentAttachment.experiment_id == experiment_id,
                        ExperimentAttachment.source_document_id == source_document_id,
                    )
                    .first()
                )
                if row is None:
                    return
                suffix = f"[datalab:{datalab_file_id}]"
                row.note = (row.note + " " + suffix).strip() if row.note else suffix
        except Exception as exc:
            logger.warning(
                "datalab ref record failed for experiment {}: {}", experiment_id, exc
            )

    def experiments_for_document(self, source_document_id: str) -> list[int]:
        with self._session_factory() as session:
            rows = (
                session.query(ExperimentAttachment.experiment_id)
                .filter(ExperimentAttachment.source_document_id == source_document_id)
                .all()
            )
            return [r[0] for r in rows]

    def count(self) -> int:
        with self._session_factory() as session:
            return int(session.query(func.count(MeasurementRow.id)).scalar() or 0)

    def failing(self, experiment_id: int | None = None) -> list[MeasurementRow]:
        """Measurements outside their spec window — the QC exception report."""
        with self._session_factory() as session:
            query = session.query(MeasurementRow).filter(MeasurementRow.passed.is_(False))
            if experiment_id is not None:
                query = query.filter(MeasurementRow.experiment_id == experiment_id)
            return query.order_by(MeasurementRow.created_at.desc()).all()


_store: MeasurementStore | None = None


def get_measurement_store() -> MeasurementStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = MeasurementStore(default_session_factory())
    return _store
