"""Sanitize + normalize material supplier records (P3).

PubChem harvests and legacy ``suppliers_json`` blobs often carry duplicate
vendors and dirty keys. This module:

* whitelists fields to ``name`` / ``url`` / ``product_url``
* dedupes within one material
* caps list length (``MAX_SUPPLIERS_PER_MATERIAL``)
* upserts into ``suppliers`` + ``material_suppliers``
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..db.models import MaterialSupplierRow, SupplierRow

MAX_SUPPLIERS_PER_MATERIAL = 50

# Accept common aliases from PubChem / older payloads, map → canonical.
_KEY_ALIASES = {
    "name": "name",
    "Name": "name",
    "SourceName": "name",
    "supplier": "name",
    "vendor": "name",
    "url": "url",
    "URL": "url",
    "SourceURL": "url",
    "homepage": "url",
    "product_url": "product_url",
    "productUrl": "product_url",
    "SourceRecordURL": "product_url",
    "product_page": "product_url",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def norm_supplier_name(name: str) -> str:
    """Stable identity key for a vendor: lower-case, collapse whitespace/punct."""
    s = (name or "").strip().lower()
    s = re.sub(r"[\s\-–—_®™()]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]


def sanitize_supplier_records(
    raw: Iterable[Any] | None,
    *,
    cap: int = MAX_SUPPLIERS_PER_MATERIAL,
) -> list[dict[str, str | None]]:
    """Return a cleaned, de-duplicated, capped supplier list.

    Unknown keys are dropped. Empty names are skipped. Dedup key is
    ``(norm_name, product_url or "")``.
    """
    if not raw:
        return []
    out: list[dict[str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        canonical: dict[str, str | None] = {
            "name": None,
            "url": None,
            "product_url": None,
        }
        for k, v in item.items():
            target = _KEY_ALIASES.get(str(k))
            if target is None or v is None:
                continue
            text = str(v).strip()
            if not text:
                continue
            if not canonical[target]:
                max_len = 120 if target == "name" else 1024
                canonical[target] = text[:max_len]
        name = canonical["name"]
        if not name:
            continue
        nk = norm_supplier_name(name)
        if not nk:
            continue
        product_url = canonical["product_url"] or ""
        key = (nk, product_url)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "name": name[:120],
                "url": canonical["url"],
                "product_url": canonical["product_url"],
            }
        )
        if len(out) >= max(1, cap):
            break
    return out


def upsert_supplier(session: Session, record: dict[str, str | None]) -> SupplierRow:
    """Insert or update a supplier master row keyed by ``norm_name``."""
    name = (record.get("name") or "").strip()
    nk = norm_supplier_name(name)
    row = session.query(SupplierRow).filter(SupplierRow.norm_name == nk).first()
    now = _utcnow()
    url = record.get("url")
    if row is None:
        row = SupplierRow(
            id=str(uuid.uuid4()),
            norm_name=nk,
            name=name[:200],
            url=(url[:1024] if url else None),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row
    changed = False
    if url and not row.url:
        row.url = url[:1024]
        changed = True
    if name and len(name) > len(row.name or ""):
        row.name = name[:200]
        changed = True
    if changed:
        row.updated_at = now
    return row


def replace_material_suppliers(
    session: Session,
    material_id: str,
    records: list[dict[str, str | None]],
    *,
    source: str = "app",
) -> int:
    """Replace all supplier links for one material. Returns link count."""
    session.query(MaterialSupplierRow).filter(
        MaterialSupplierRow.material_id == material_id
    ).delete(synchronize_session=False)
    count = 0
    now = _utcnow()
    for rec in records:
        supplier = upsert_supplier(session, rec)
        product_url = (rec.get("product_url") or "")[:1024]
        session.add(
            MaterialSupplierRow(
                id=str(uuid.uuid4()),
                material_id=material_id,
                supplier_id=supplier.id,
                product_url=product_url,
                source=(source or "app")[:32],
                created_at=now,
            )
        )
        count += 1
    session.flush()
    return count


def fetch_suppliers_for_materials(
    session: Session, material_ids: list[str]
) -> dict[str, list[dict[str, str | None]]]:
    """Bulk-load normalized suppliers keyed by material_id."""
    if not material_ids:
        return {}
    rows = (
        session.query(MaterialSupplierRow, SupplierRow)
        .join(SupplierRow, MaterialSupplierRow.supplier_id == SupplierRow.id)
        .filter(MaterialSupplierRow.material_id.in_(material_ids))
        .order_by(SupplierRow.name)
        .all()
    )
    out: dict[str, list[dict[str, str | None]]] = {mid: [] for mid in material_ids}
    for link, supplier in rows:
        out.setdefault(link.material_id, []).append(
            {
                "name": supplier.name,
                "url": supplier.url,
                "product_url": link.product_url or None,
            }
        )
    return out


def sync_material_suppliers_from_json(
    session: Session,
    material_id: str,
    raw: Any,
    *,
    source: str = "app",
    clear_json_projection: bool = False,
) -> list[dict[str, str | None]]:
    """Sanitize ``raw``, write link tables, return projection for dual-write."""
    cleaned = sanitize_supplier_records(raw)
    replace_material_suppliers(session, material_id, cleaned, source=source)
    if clear_json_projection:
        return []
    return cleaned
