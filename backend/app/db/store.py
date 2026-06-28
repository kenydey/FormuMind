"""Experiment persistence stores.

Three interchangeable backends implement the same small interface so the
``ModelRegistry`` is agnostic to where data lives:

* :class:`DatalabExperimentStore` — Datalab Headless ELN SSOT (``formumind_training``
  block) with SQLite index rows (production default when ``experiment_backend=datalab``).
* :class:`SqlExperimentStore` — SQLAlchemy-backed inline JSON (sqlite fallback / tests).
* :class:`JsonExperimentStore` — single-file JSON store for lightweight tests and migration.

Interface: ``all() -> list[ExperimentRecord]``, ``add(records)``, ``clear()``.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..domain.schemas import ExperimentRecord, ProductDomain
from .datalab_client import (
    DatalabStoreError,
    DatalabUnavailableError,
    check_datalab_reachable,
    parse_create_sample_response,
    parse_delete_response,
    parse_item_envelope,
    validate_blocks,
)
from .models import ExperimentRow

logger = logging.getLogger(__name__)

_TRAINING_BLOCK = "formumind_training"
_TRAINING_BLOCKS = (_TRAINING_BLOCK,)


class ExperimentStore(Protocol):
    def all(self) -> list[ExperimentRecord]: ...
    def add(self, records: list[ExperimentRecord]) -> None: ...
    def clear(self) -> None: ...
    def close(self) -> None: ...


def _new_training_item_id() -> str:
    return f"formumind_exp_{uuid.uuid4().hex[:8]}"


def _training_block_data(rec: ExperimentRecord) -> dict[str, Any]:
    return {
        "domain": rec.domain.value,
        "project_id": rec.project_id,
        "factors": rec.factors,
        "cure_temperature_c": rec.cure_temperature_c,
        "measured": rec.measured,
        "source": rec.source,
        "label": rec.label,
    }


def _blocks_for_training(rec: ExperimentRecord) -> dict[str, Any]:
    return {
        _TRAINING_BLOCK: {
            "block_id": _TRAINING_BLOCK,
            "block_type": "generic",
            "data": _training_block_data(rec),
        }
    }


def _record_from_item_data(item_data: dict[str, Any]) -> ExperimentRecord:
    validate_blocks(item_data, _TRAINING_BLOCKS)
    data = (item_data.get("blocks_obj") or {}).get(_TRAINING_BLOCK, {}).get("data") or {}
    return ExperimentRecord(
        domain=ProductDomain(data["domain"]),
        project_id=str(data.get("project_id") or ""),
        factors=dict(data.get("factors") or {}),
        cure_temperature_c=data.get("cure_temperature_c"),
        measured=dict(data.get("measured") or {}),
        source=str(data.get("source") or "lab"),
        label=str(data.get("label") or ""),
    )


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

    def close(self) -> None:
        return None


def _row_to_record(row: ExperimentRow) -> ExperimentRecord:
    return ExperimentRecord(
        domain=row.domain,
        project_id=row.project_id or "",
        factors=row.factors or {},
        cure_temperature_c=row.cure_temperature_c,
        measured=row.measured,
        source=row.source,
        label=row.label,
    )


def _record_to_row(rec: ExperimentRecord) -> ExperimentRow:
    return ExperimentRow(
        domain=rec.domain.value,
        project_id=rec.project_id,
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
        self._write_lock = threading.Lock()

    def all(self) -> list[ExperimentRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ExperimentRow)
                .where(ExperimentRow.item_id.is_(None))
                .order_by(ExperimentRow.id)
            ).all()
            return [_row_to_record(r) for r in rows]

    def add(self, records: list[ExperimentRecord]) -> None:
        if not records:
            return
        with self._write_lock, self._session_factory() as session:
            session.add_all([_record_to_row(r) for r in records])
            session.commit()

    def clear(self) -> None:
        with self._write_lock, self._session_factory() as session:
            session.execute(delete(ExperimentRow).where(ExperimentRow.item_id.is_(None)))
            session.commit()

    def count(self) -> int:
        with self._session_factory() as session:
            return len(
                session.scalars(select(ExperimentRow.id).where(ExperimentRow.item_id.is_(None))).all()
            )

    def close(self) -> None:
        return None


class DatalabExperimentStore:
    """Sync httpx proxy to Datalab Headless ELN (SSOT for training records)."""

    def __init__(
        self,
        api_url: str,
        session_factory: sessionmaker[Session],
        *,
        timeout: float = 30.0,
        max_connections: int = 10,
        max_keepalive_connections: int = 5,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._session_factory = session_factory
        self._timeout = timeout
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )
        self._client: httpx.Client | None = None
        self._write_lock = threading.Lock()

    def _ensure_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self._api_url,
                timeout=self._timeout,
                limits=self._limits,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            self._client.close()
        self._client = None

    def _create_sample(self, sample_data: dict[str, Any]) -> None:
        expected_id = str(sample_data["item_id"])
        payload = {"new_sample_data": sample_data, "generate_id_automatically": False}
        logger.info("Datalab POST /new-sample/ item_id=%s", expected_id)
        client = self._ensure_client()
        resp = client.post("/new-sample/", json=payload)
        resp.raise_for_status()
        parse_create_sample_response(resp.json(), expected_id)

    def _get_item(self, item_id: str) -> dict[str, Any]:
        client = self._ensure_client()
        resp = client.get(f"/get-item-data/{item_id}")
        resp.raise_for_status()
        return parse_item_envelope(resp.json(), required_blocks=_TRAINING_BLOCKS)

    def _delete_sample(self, item_id: str) -> None:
        client = self._ensure_client()
        resp = client.post("/delete-sample/", json={"item_id": item_id})
        resp.raise_for_status()
        parse_delete_response(resp.json(), item_id)

    def _rollback_created_samples(self, item_ids: list[str]) -> None:
        for item_id in reversed(item_ids):
            try:
                self._delete_sample(item_id)
                logger.info("Saga rollback: deleted training sample %s", item_id)
            except Exception as exc:
                logger.error("Saga rollback failed for %s: %s", item_id, exc)

    def all(self) -> list[ExperimentRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ExperimentRow)
                .where(ExperimentRow.item_id.isnot(None))
                .order_by(ExperimentRow.id)
            ).all()
        out: list[ExperimentRecord] = []
        for row in rows:
            item_data = self._get_item(str(row.item_id))
            out.append(_record_from_item_data(item_data))
        return out

    def add(self, records: list[ExperimentRecord]) -> None:
        if not records:
            return
        created_item_ids: list[str] = []
        try:
            with self._write_lock, self._session_factory() as session:
                for rec in records:
                    item_id = _new_training_item_id()
                    sample_data = {
                        "item_id": item_id,
                        "name": rec.label or f"Training {rec.domain.value}",
                        "description": "FormuMind experiment training record",
                        "type": ["samples"],
                        "blocks_obj": _blocks_for_training(rec),
                        "display_order": [_TRAINING_BLOCK],
                    }
                    created_item_ids.append(item_id)
                    self._create_sample(sample_data)
                    session.add(
                        ExperimentRow(
                            item_id=item_id,
                            domain=rec.domain.value,
                            project_id=rec.project_id,
                            factors={},
                            measured={},
                            source=rec.source,
                            label=rec.label,
                        )
                    )
                session.commit()
        except Exception as exc:
            logger.error(
                "DatalabExperimentStore.add failed after %d/%d samples: %s",
                len(created_item_ids),
                len(records),
                exc,
            )
            self._rollback_created_samples(created_item_ids)
            raise

    def clear(self) -> None:
        with self._session_factory() as session:
            item_ids = [
                str(row.item_id)
                for row in session.scalars(
                    select(ExperimentRow).where(ExperimentRow.item_id.isnot(None))
                ).all()
                if row.item_id
            ]
        self._rollback_created_samples(item_ids)
        with self._write_lock, self._session_factory() as session:
            session.execute(delete(ExperimentRow).where(ExperimentRow.item_id.isnot(None)))
            session.commit()

    def count(self) -> int:
        with self._session_factory() as session:
            return len(
                session.scalars(select(ExperimentRow.id).where(ExperimentRow.item_id.isnot(None))).all()
            )


_store: ExperimentStore | None = None


def get_experiment_store(settings: Settings | None = None) -> ExperimentStore:
    global _store
    if _store is not None:
        return _store
    s = settings or get_settings()
    from .database import default_session_factory

    factory = default_session_factory()
    backend = (s.experiment_backend or "sqlite").lower()
    if backend == "datalab":
        ok, reason = check_datalab_reachable(
            s.datalab_api_url,
            timeout=min(2.0, s.datalab_timeout_seconds),
        )
        if not ok:
            raise DatalabUnavailableError(s.datalab_api_url, reason)
        _store = DatalabExperimentStore(
            s.datalab_api_url,
            factory,
            timeout=s.datalab_timeout_seconds,
            max_connections=s.datalab_max_connections,
            max_keepalive_connections=s.datalab_max_keepalive_connections,
        )
    else:
        if s.environment == "production":
            logger.warning(
                "SqlExperimentStore is deprecated for production; set FORMUMIND_EXPERIMENT_BACKEND=datalab"
            )
        _store = SqlExperimentStore(factory)
    return _store


def reset_experiment_store(store: ExperimentStore | None = None) -> None:
    """Test helper — inject a store or clear the singleton."""
    global _store
    _store = store
