"""Semi-automatic material catalog expansion.

High-confidence candidates (CAS / SMILES present) upsert into ``materials``
with ``origin=kb_promoted|requirement|formula|workbench``. Low-confidence
names land in ``material_candidates`` for human promote / dismiss.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..db.material_store import get_material_store, norm_key
from ..db.models import MaterialCandidateRow
from ..db.session_utils import commit_session
from ..domain.knowledge import RAW_MATERIALS
from ..services.errors import degrade_return

_HIGH_ORIGIN = {"kb_promoted", "requirement", "formula", "workbench", "user", "import"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _has_identity(spec: dict[str, Any]) -> bool:
    return bool(str(spec.get("cas_no") or "").strip() or str(spec.get("smiles") or "").strip())


class MaterialCandidateStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert_pending(
        self,
        name: str,
        spec: dict[str, Any],
        *,
        source: str,
        source_ref: str = "",
        confidence: str = "low",
    ) -> bool:
        display = (name or "").strip()
        if not display:
            return False
        key = norm_key(display)
        try:
            with commit_session(self._session_factory) as session:
                row = (
                    session.query(MaterialCandidateRow)
                    .filter(MaterialCandidateRow.norm_key == key)
                    .first()
                )
                if row is None:
                    row = MaterialCandidateRow(
                        id=str(uuid.uuid4()),
                        norm_key=key,
                        name=display[:200],
                        status="pending",
                        created_at=_utcnow(),
                    )
                    session.add(row)
                elif row.status == "dismissed":
                    # Re-open dismissed items when seen again from a new source.
                    row.status = "pending"
                elif row.status == "promoted":
                    return False
                for field in (
                    "role",
                    "cas_no",
                    "smiles",
                    "formula",
                    "zh_name",
                    "supplier",
                ):
                    val = spec.get(field)
                    if val in (None, ""):
                        continue
                    if not getattr(row, field, None):
                        setattr(row, field, str(val)[:200] if field != "smiles" else str(val))
                row.source = (source or "")[:64]
                if source_ref:
                    row.source_ref = source_ref[:512]
                row.confidence = confidence if confidence in {"high", "low"} else "low"
                row.updated_at = _utcnow()
        except IntegrityError:
            return False
        except Exception as exc:
            return degrade_return(logger, exc, f"candidate upsert failed: {display}", False)
        return True

    def list_pending(self, limit: int = 200) -> list[MaterialCandidateRow]:
        with self._session_factory() as session:
            return (
                session.query(MaterialCandidateRow)
                .filter(MaterialCandidateRow.status == "pending")
                .order_by(MaterialCandidateRow.updated_at.desc())
                .limit(limit)
                .all()
            )

    def get(self, candidate_id: str) -> MaterialCandidateRow | None:
        with self._session_factory() as session:
            return (
                session.query(MaterialCandidateRow)
                .filter(MaterialCandidateRow.id == candidate_id)
                .first()
            )

    def set_status(self, candidate_id: str, status: str) -> bool:
        try:
            with commit_session(self._session_factory) as session:
                row = (
                    session.query(MaterialCandidateRow)
                    .filter(MaterialCandidateRow.id == candidate_id)
                    .first()
                )
                if row is None:
                    return False
                row.status = status
                row.updated_at = _utcnow()
        except Exception as exc:
            return degrade_return(logger, exc, f"candidate status failed: {candidate_id}", False)
        return True


_candidates: MaterialCandidateStore | None = None


def get_candidate_store() -> MaterialCandidateStore:
    global _candidates
    if _candidates is None:
        from ..db.database import default_session_factory

        _candidates = MaterialCandidateStore(default_session_factory())
    return _candidates


def propose_material(
    name: str,
    spec: dict[str, Any] | None = None,
    *,
    source: str = "kb_promoted",
    source_ref: str = "",
    force_pending: bool = False,
) -> dict[str, Any]:
    """Promote high-confidence materials; queue the rest for review.

    Returns ``{"action": "upsert"|"pending"|"skipped"|"exists", "name": ...}``.
    """
    display = (name or "").strip()
    if not display:
        return {"action": "skipped", "name": "", "reason": "empty"}
    payload = dict(spec or {})
    payload.setdefault("role", payload.get("role") or "")
    # Already in live catalog?
    if display in RAW_MATERIALS or any(norm_key(k) == norm_key(display) for k in RAW_MATERIALS):
        return {"action": "exists", "name": display}

    high = (not force_pending) and _has_identity(payload)
    if high:
        store = get_material_store()
        origin = source if source in _HIGH_ORIGIN else "kb_promoted"
        ok = store.upsert(display, payload, origin=origin, overwrite=False)
        if ok:
            RAW_MATERIALS.refresh()
            return {"action": "upsert", "name": display, "origin": origin}
        return {"action": "skipped", "name": display, "reason": "upsert_failed"}

    cand = get_candidate_store()
    ok = cand.upsert_pending(
        display,
        payload,
        source=source,
        source_ref=source_ref,
        confidence="high" if _has_identity(payload) else "low",
    )
    return {
        "action": "pending" if ok else "skipped",
        "name": display,
        "reason": "" if ok else "candidate_failed",
    }


def promote_candidate(candidate_id: str) -> dict[str, Any]:
    cand = get_candidate_store()
    row = cand.get(candidate_id)
    if row is None:
        return {"ok": False, "reason": "not_found"}
    if row.status == "promoted":
        return {"ok": True, "action": "exists", "name": row.name}
    spec = {
        "role": row.role or "",
        "cas_no": row.cas_no or None,
        "smiles": row.smiles or None,
        "formula": row.formula or None,
        "zh_name": row.zh_name or None,
        "supplier": row.supplier or None,
        "availability": "in_stock",
    }
    store = get_material_store()
    origin = row.source if row.source in _HIGH_ORIGIN else "kb_promoted"
    ok = store.upsert(row.name, {k: v for k, v in spec.items() if v is not None}, origin=origin)
    if not ok:
        return {"ok": False, "reason": "upsert_failed"}
    cand.set_status(candidate_id, "promoted")
    RAW_MATERIALS.refresh()
    return {"ok": True, "action": "upsert", "name": row.name, "origin": origin}


def dismiss_candidate(candidate_id: str) -> dict[str, Any]:
    ok = get_candidate_store().set_status(candidate_id, "dismissed")
    return {"ok": ok}


def propose_many(
    items: Iterable[dict[str, Any]],
    *,
    source: str,
    source_ref: str = "",
) -> dict[str, int]:
    counts = {"upsert": 0, "pending": 0, "exists": 0, "skipped": 0}
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            counts["skipped"] += 1
            continue
        result = propose_material(
            name,
            item,
            source=source,
            source_ref=source_ref,
        )
        counts[result.get("action", "skipped")] = counts.get(result.get("action", "skipped"), 0) + 1
    return counts


def promote_kb_products(limit: int = 200) -> dict[str, int]:
    """Harvest ``kb_products`` into the catalog / pending queue."""
    try:
        from ..db.product_store import get_product_store

        store = get_product_store()
        products = store.search("", limit=limit)
    except Exception as exc:
        logger.debug("promote_kb_products: product store unavailable ({})", exc)
        return {"upsert": 0, "pending": 0, "exists": 0, "skipped": 0}

    items: list[dict[str, Any]] = []
    for p in products:
        trade = getattr(p, "trade_name", "") or ""
        generic = getattr(p, "generic_name", "") or ""
        name = (generic or trade).strip()
        if not name:
            continue
        items.append(
            {
                "name": name,
                "role": getattr(p, "role", "") or "",
                "cas_no": getattr(p, "cas", "") or None,
                "smiles": getattr(p, "smiles", None),
                "supplier": getattr(p, "supplier", "") or None,
            }
        )
    return propose_many(items, source="kb_promoted", source_ref="kb_products")


def candidate_to_dict(row: MaterialCandidateRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "role": row.role or "",
        "cas_no": row.cas_no or "",
        "smiles": row.smiles or "",
        "formula": row.formula or "",
        "zh_name": row.zh_name or "",
        "supplier": row.supplier or "",
        "source": row.source or "",
        "source_ref": row.source_ref or "",
        "confidence": row.confidence or "low",
        "status": row.status,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
