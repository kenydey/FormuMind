"""Atomic QC-report ingest: store the report, extract measurements, bind both.

Mirrors ``ingest_tx.ingest_document_tx``: one caller-owned session, savepoint
upsert of the parent row, ``*_in`` store calls that join the transaction, and a
single commit. Either the report, its measurements and the experiment binding
all land, or none of them do — a half-attached certificate is worse than none,
because it looks like the run was verified.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ..domain.schemas import Measurement


@dataclass
class QCIngestResult:
    source_id: str
    experiment_id: int
    measurements: list[Measurement] = field(default_factory=list)
    attachment_id: str | None = None
    already_attached: bool = False
    extraction_error: str | None = None
    report_meta: dict = field(default_factory=dict)

    @property
    def measurement_count(self) -> int:
        return len(self.measurements)


def ingest_qc_report_tx(
    session_factory: sessionmaker[Session],
    *,
    experiment_id: int,
    filename: str,
    text: str,
    title: str = "",
    project_id: str | None = None,
    source_id: str | None = None,
) -> QCIngestResult:
    """Ingest a parsed QC report and bind it to ``experiment_id``.

    Raises ``ValueError`` when the experiment does not exist — checked up front
    rather than relying on the foreign key, so the caller gets a 404 instead of
    a 500.
    """
    from ..db.measurement_store import get_measurement_store
    from ..db.models import ExperimentRow, SourceDocument
    from ..db.session_utils import commit_session
    from .qc_report import extract_qc_report

    extraction, err = extract_qc_report(text, title=title or filename)
    store = get_measurement_store()
    content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

    with commit_session(session_factory) as session:
        try:
            if session.get(ExperimentRow, experiment_id) is None:
                raise ValueError(f"experiment {experiment_id} not found")

            # Re-uploading the same certificate is not a second report. Without
            # this the fresh UUID below would defeat uq_experiment_attachment
            # and the experiment would accumulate duplicate bindings.
            doc_id = source_id
            if doc_id is None:
                existing = (
                    session.query(SourceDocument)
                    .filter(
                        SourceDocument.content_hash == content_hash,
                        SourceDocument.source_kind == "qc_report",
                    )
                    .first()
                )
                doc_id = existing.id if existing is not None else str(uuid.uuid4())

            # ── 1. the report itself ──
            doc = session.get(SourceDocument, doc_id)
            if doc is None:
                savepoint = session.begin_nested()
                try:
                    session.add(
                        SourceDocument(
                            id=doc_id,
                            filename=filename,
                            title=title or filename,
                            source_kind="qc_report",
                            full_text=text,
                            content_hash=content_hash,
                            raw_text_chars=len(text),
                            project_id=project_id,
                            extraction_status="ok" if not err else "degraded",
                            extraction_error=err,
                        )
                    )
                    session.flush()
                    savepoint.commit()
                except IntegrityError:
                    savepoint.rollback()
                    if session.get(SourceDocument, doc_id) is None:
                        raise
                except OperationalError:
                    raise

            # ── 2. the binding that was missing ──
            # Attach first: a None result means this report is already bound to
            # this experiment, so its measurements are already recorded and
            # re-inserting them would double-count the same certificate.
            attachment_id = store.attach_in(
                session,
                experiment_id,
                doc_id,
                kind="qc_report",
                note=extraction.notes[:200],
            )

            # ── 3. typed measurements ──
            measurements = extraction.to_measurements(source_document_id=doc_id)
            if measurements and attachment_id is not None:
                store.add_in(session, experiment_id, measurements, source_document_id=doc_id)

        except Exception:
            session.rollback()
            raise

    return QCIngestResult(
        source_id=doc_id,
        experiment_id=experiment_id,
        measurements=measurements,
        attachment_id=attachment_id,
        already_attached=attachment_id is None,
        extraction_error=err,
        report_meta={
            "report_id": extraction.report_id,
            "sample_id": extraction.sample_id,
            "tested_at": extraction.tested_at,
            "laboratory": extraction.laboratory,
            "confidence": extraction.confidence,
        },
    )


def sync_measurements_to_experiment(experiment_id: int) -> dict[str, Any]:
    """Fold typed measurements back into the experiment's flat ``measured`` JSON.

    The training pipeline reads that column, so a QC report only becomes
    training data once this runs. Typed rows win over any pre-existing value:
    a certificate is more authoritative than a hand-typed cell.
    """
    from ..db.database import default_session_factory
    from ..db.measurement_store import get_measurement_store
    from ..db.models import ExperimentRow
    from ..db.session_utils import commit_session

    measurements = get_measurement_store().for_experiment(experiment_id)
    if not measurements:
        return {}
    factory = default_session_factory()
    try:
        with commit_session(factory) as session:
            row = session.get(ExperimentRow, experiment_id)
            if row is None:
                return {}
            merged = dict(row.measured or {})
            merged.update({m.metric: m.value for m in measurements})
            row.measured = merged
            return merged
    except Exception as exc:
        logger.warning("measured sync failed for experiment {}: {}", experiment_id, exc)
        return {"_sync_error": str(exc), "experiment_id": experiment_id}
