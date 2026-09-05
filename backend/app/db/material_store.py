"""Raw-material registry (materials table).

Backs ``knowledge.RAW_MATERIALS``. Seeded from the curated literal, then grown
at runtime by manual entries and by promoting trade products harvested out of
ingested literature (``kb_products``).

Mirrors ``product_store``: per-item commits so one bad row can't abort a batch,
an ``IntegrityError`` retry for the concurrent-insert race, and blank-only field
merging so a harvested row can never overwrite curated chemistry.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..services.errors import degrade_return
from .models import MaterialRow
from .session_utils import commit_session

# Spec keys that map 1:1 onto columns. Order is irrelevant; membership is not.
_SPEC_FIELDS = (
    "role", "formula", "smiles", "cas_no", "zh_name", "molar_mass",
    "price_cny_per_kg", "voc_contrib", "density_gcm3", "oil_absorption",
    "tg_k", "lab", "svhc", "carrier", "water_compatible",
    "functional_class", "equivalent_weight", "hansen_d", "hansen_p",
    "hansen_h", "hlb", "supplier", "lead_time_days", "availability",
    "regulatory", "substitute_group",
)
_TEXT_WIDTHS = {
    "role": 60, "formula": 120, "cas_no": 32, "zh_name": 200, "carrier": 16,
    "functional_class": 60, "supplier": 120, "availability": 16,
    "substitute_group": 60,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def norm_key(name: str) -> str:
    """Normalized material key: strip separators and case."""
    return re.sub(r"[\s\-–—_®™()]+", "", (name or "").lower())[:200]


def _coerce(field: str, value):
    width = _TEXT_WIDTHS.get(field)
    if width is not None and value is not None:
        return str(value)[:width]
    return value


def _apply_spec(row: MaterialRow, spec: dict, *, overwrite: bool) -> None:
    """Merge a spec dict onto a row.

    ``overwrite=False`` (the default for harvested/enrichment writes) only fills
    columns that are currently blank, so curated values always win.
    """
    for field in _SPEC_FIELDS:
        if field not in spec:
            continue
        value = spec[field]
        if value is None:
            continue
        if not overwrite and getattr(row, field, None) not in (None, "", []):
            continue
        setattr(row, field, _coerce(field, value))
    row.updated_at = _utcnow()


class MaterialStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        # Bumped on every write; MaterialCatalog uses it to invalidate its
        # in-process snapshot without querying on the read path.
        self.generation = 0

    # ── writes ─────────────────────────────────────────────────────────────

    def upsert(
        self,
        name: str,
        spec: dict,
        *,
        origin: str = "seed",
        overwrite: bool = True,
    ) -> bool:
        """Insert or merge one material. Returns True when the row was touched."""
        display = (name or "").strip()
        if not display:
            return False
        key = norm_key(display)
        try:
            with commit_session(self._session_factory) as session:
                row = session.query(MaterialRow).filter(MaterialRow.norm_key == key).first()
                if row is None:
                    row = MaterialRow(
                        id=str(uuid.uuid4()),
                        norm_key=key,
                        name=display[:200],
                        origin=origin,
                        created_at=_utcnow(),
                    )
                    session.add(row)
                _apply_spec(row, spec, overwrite=overwrite)
        except IntegrityError:
            with commit_session(self._session_factory) as session:
                row = session.query(MaterialRow).filter(MaterialRow.norm_key == key).first()
                if row is not None:
                    _apply_spec(row, spec, overwrite=overwrite)
        except Exception as exc:
            return degrade_return(logger, exc, f"material upsert failed: {display}", False)
        self.generation += 1
        return True

    def seed_missing(self, materials: dict[str, dict]) -> int:
        """Insert curated materials that aren't in the table yet.

        Idempotent and non-destructive: an existing row (possibly carrying
        user edits or PubChem-backfilled SMILES) is left untouched.
        """
        written = 0
        try:
            with self._session_factory() as session:
                existing = {r.norm_key for r in session.query(MaterialRow.norm_key).all()}
        except Exception as exc:
            return degrade_return(logger, exc, "material seed: read failed", 0)
        for name, spec in materials.items():
            if norm_key(name) in existing:
                continue
            if self.upsert(name, spec, origin="seed"):
                written += 1
        if written:
            logger.info("Seeded material catalog: {} material(s)", written)
        return written

    def set_availability(self, name: str, availability: str) -> bool:
        """Flag a material in_stock / restricted / discontinued."""
        return self.upsert(name, {"availability": availability}, overwrite=True)

    # ── reads ──────────────────────────────────────────────────────────────

    def list_all(self, limit: int | None = None) -> list[MaterialRow]:
        with self._session_factory() as session:
            query = session.query(MaterialRow).order_by(MaterialRow.created_at, MaterialRow.name)
            if limit is not None:
                query = query.limit(limit)
            return query.all()

    def get(self, name: str) -> MaterialRow | None:
        with self._session_factory() as session:
            return (
                session.query(MaterialRow)
                .filter(MaterialRow.norm_key == norm_key(name))
                .first()
            )

    def search(
        self,
        q: str = "",
        *,
        role: str = "",
        availability: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[MaterialRow]:
        with self._session_factory() as session:
            query = session.query(MaterialRow)
            term = (q or "").strip()
            if term:
                like = f"%{_escape_like(term)}%"
                query = query.filter(
                    or_(
                        MaterialRow.name.ilike(like, escape="\\"),
                        MaterialRow.zh_name.ilike(like, escape="\\"),
                        MaterialRow.cas_no.ilike(like, escape="\\"),
                        MaterialRow.supplier.ilike(like, escape="\\"),
                    )
                )
            if role:
                query = query.filter(MaterialRow.role == role)
            if availability:
                query = query.filter(MaterialRow.availability == availability)
            return query.order_by(MaterialRow.name).offset(offset).limit(limit).all()

    def count(self) -> int:
        with self._session_factory() as session:
            return int(session.query(func.count(MaterialRow.id)).scalar() or 0)

    @staticmethod
    def row_to_spec(row: MaterialRow) -> dict:
        """ORM row → a ``RAW_MATERIALS``-shaped spec dict.

        NULL columns are **omitted**, not emitted as None: consumers read with
        ``.get(key, default)`` and a stored None would defeat the default
        (``carrier`` → "both", most visibly).
        """
        spec: dict = {}
        for field in _SPEC_FIELDS:
            value = getattr(row, field, None)
            if value is None:
                continue
            spec[field] = value
        return spec


_store: MaterialStore | None = None


def get_material_store() -> MaterialStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = MaterialStore(default_session_factory())
    return _store
