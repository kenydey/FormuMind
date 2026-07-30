"""Corpus-level commercial product registry (kb_products table).

Every ingest path funnels product mentions here:

* rule-tier chunk extraction (``chem_extract.extract_products``);
* the LLM source guide (``SourceGuideSchema.products``).

Upserts are idempotent per normalized ``trade|grade`` key: blank fields fill
in over time (a patent may name the supplier, a paper the generic chemical),
mention counts accumulate, and source ids keep provenance.  Structure linking
(牌号 → CAS/SMILES via PubChem synonyms) is best-effort through the chemtools
gateway and never blocks ingestion.
"""
from __future__ import annotations

import concurrent.futures
import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..services.errors import degrade_return
from .models import KBProduct
from .session_utils import commit_session

logger = logging.getLogger(__name__)

_MAX_SOURCE_IDS = 50
_MAX_LINK_ATTEMPTS_PER_UPSERT = 5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _apply_mention(row: KBProduct, m: dict, source_id: str | None) -> None:
    """Fill blank product fields, bump mention_count, track source_ids."""
    if not row.supplier and m.get("supplier"):
        row.supplier = str(m["supplier"])[:120]
    if not row.generic_name and m.get("generic_name"):
        row.generic_name = str(m["generic_name"])[:200]
    if not row.cas and m.get("cas"):
        row.cas = str(m["cas"])[:32]
    if not row.smiles and m.get("smiles"):
        row.smiles = str(m["smiles"])
    if not row.role and m.get("role"):
        row.role = str(m["role"])[:60]
    row.mention_count = (row.mention_count or 0) + 1
    ids = list(row.source_ids or [])
    if source_id and source_id not in ids:
        if len(ids) < _MAX_SOURCE_IDS:
            ids.append(source_id)
            row.source_ids = ids
        else:
            logger.warning(
                "product %s exceeded max source_ids (%d), dropping source %s",
                row.norm_key,
                _MAX_SOURCE_IDS,
                source_id,
            )
    row.updated_at = _utcnow()


def norm_key(trade_name: str, grade: str = "") -> str:
    base = re.sub(r"[\s\-–—_®™]+", "", (trade_name or "").lower())
    g = re.sub(r"[\s\-–—_]+", "", (grade or "").lower())
    return f"{base}|{g}"[:200]


class ProductStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert_mentions(
        self, source_id: str | None, mentions: list[dict], *, link_structures: bool = True
    ) -> int:
        """Merge product mentions into the registry; returns rows touched."""
        touched = 0
        link_budget = _MAX_LINK_ATTEMPTS_PER_UPSERT if link_structures else 0
        pending_links: list[tuple[str, str, str, str]] = []
        for m in mentions or []:
            trade = (m.get("trade_name") or "").strip()
            if not trade:
                continue
            grade = (m.get("grade") or "").strip()
            key = norm_key(trade, grade)
            try:
                with commit_session(self._session_factory) as session:
                    row = (
                        session.query(KBProduct).filter(KBProduct.norm_key == key).first()
                    )
                    if row is None:
                        row = KBProduct(
                            id=str(uuid.uuid4()),
                            norm_key=key,
                            trade_name=trade[:120],
                            grade=grade[:60],
                            mention_count=0,
                            source_ids=[],
                            first_seen=_utcnow(),
                        )
                        session.add(row)
                    _apply_mention(row, m, source_id)
                    needs_link = not row.cas and not row.smiles
                touched += 1
                # Deferred rather than resolved here: each lookup is a network
                # round trip, and doing them inline made this loop the single
                # largest cost in the whole KB build (measured: 7 mentions on
                # one coating patent = 6537 ms, 61% of the batch).
                if needs_link and len(pending_links) < link_budget:
                    pending_links.append((key, trade, grade, m.get("generic_name") or ""))
            except IntegrityError:
                with commit_session(self._session_factory) as session:
                    row = (
                        session.query(KBProduct).filter(KBProduct.norm_key == key).first()
                    )
                    if row is not None:
                        _apply_mention(row, m, source_id)
                touched += 1
            except Exception as exc:
                degrade_return(logger, exc, f"product upsert failed: {trade}", None)

        self._link_structures(pending_links)
        return touched

    @staticmethod
    def _resolve_structure(trade: str, grade: str, generic: str) -> tuple[str | None, str | None]:
        """Network half of structure linking: 牌号 → (CAS, SMILES). No DB access."""
        try:
            from ..services import chemtools

            queries = [f"{trade} {grade}".strip(), generic.strip()]
            cas = smiles = None
            for q in queries:
                if not q:
                    continue
                cas = cas or chemtools.name_to_cas(q)
                smiles = smiles or chemtools.name_to_smiles(q)
                if cas or smiles:
                    break
            return cas, smiles
        except Exception as exc:
            return degrade_return(logger, exc, "product structure lookup failed", (None, None))

    def _link_structures(self, pending: list[tuple[str, str, str, str]]) -> None:
        """Resolve structures concurrently, then write the results serially.

        Split deliberately: the lookups are independent network round trips and
        gain everything from overlapping, while the writes stay on one thread so
        the SQLite access pattern is exactly what it was before.
        """
        if not pending:
            return
        try:
            from ..services import chemtools

            if not chemtools.gateway_enabled():
                return
        except Exception as exc:
            degrade_return(logger, exc, "chemtools gateway probe failed", None)
            return

        results: list[tuple[str, str | None, str | None]] = []
        if len(pending) == 1:
            key, trade, grade, generic = pending[0]
            cas, smiles = self._resolve_structure(trade, grade, generic)
            results.append((key, cas, smiles))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(pending))) as ex:
                futures = {
                    ex.submit(self._resolve_structure, trade, grade, generic): key
                    for key, trade, grade, generic in pending
                }
                for fut in concurrent.futures.as_completed(futures):
                    key = futures[fut]
                    try:
                        cas, smiles = fut.result()
                    except Exception as exc:
                        degrade_return(logger, exc, "product structure lookup failed", None)
                        continue
                    results.append((key, cas, smiles))

        for key, cas, smiles in results:
            if not cas and not smiles:
                continue
            try:
                with commit_session(self._session_factory) as session:
                    row = session.query(KBProduct).filter(KBProduct.norm_key == key).first()
                    if row is None:
                        continue
                    if cas and not row.cas:
                        row.cas = cas[:32]
                    if smiles and not row.smiles:
                        row.smiles = smiles
            except Exception as exc:
                degrade_return(logger, exc, "product structure link failed", None)

    def _link_structure(self, key: str, trade: str, grade: str, generic: str) -> None:
        """Single-product structure link (kept for callers outside the ingest path)."""
        self._link_structures([(key, trade, grade, generic)])

    def search(self, q: str = "", limit: int = 50, offset: int = 0) -> list[KBProduct]:
        with self._session_factory() as session:
            query = session.query(KBProduct)
            term = (q or "").strip()
            if term:
                like = f"%{_escape_like(term)}%"
                query = query.filter(
                    or_(
                        KBProduct.trade_name.ilike(like, escape="\\"),
                        KBProduct.supplier.ilike(like, escape="\\"),
                        KBProduct.generic_name.ilike(like, escape="\\"),
                        KBProduct.cas.ilike(like, escape="\\"),
                    )
                )
            return (
                query.order_by(KBProduct.mention_count.desc(), KBProduct.trade_name)
                .offset(offset)
                .limit(limit)
                .all()
            )

    def find_for_material(self, name: str, cas: str = "") -> list[KBProduct]:
        """Products linked to a generic material (name or CAS) — recommend hook."""
        term = (name or "").strip()
        with self._session_factory() as session:
            query = session.query(KBProduct)
            conds = []
            if cas:
                conds.append(KBProduct.cas == cas)
            if term:
                conds.append(
                    KBProduct.generic_name.ilike(f"%{_escape_like(term)}%", escape="\\")
                )
            if not conds:
                return []
            return (
                query.filter(or_(*conds))
                .order_by(KBProduct.mention_count.desc())
                .limit(10)
                .all()
            )

    def count(self) -> int:
        with self._session_factory() as session:
            return int(session.query(func.count(KBProduct.id)).scalar() or 0)


_store: ProductStore | None = None


def get_product_store() -> ProductStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = ProductStore(default_session_factory())
    return _store
