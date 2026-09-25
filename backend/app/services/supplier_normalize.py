"""Sanitize + normalize material supplier records (P3 + A′ manual quotes).

PubChem harvests and legacy ``suppliers_json`` blobs often carry duplicate
vendors and dirty keys. This module:

* whitelists identity + optional manual commercial fields
* dedupes within one material
* caps list length (``MAX_SUPPLIERS_PER_MATERIAL``)
* upserts into ``suppliers`` + ``material_suppliers``
* annotates ``stale_price`` for UI (display only)
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..db.models import MaterialSupplierRow, SupplierRow

MAX_SUPPLIERS_PER_MATERIAL = 50
# Prices older than this (or missing observed_at when price set) → stale_price.
STALE_PRICE_DAYS = 180

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
    # A′ manual commercial
    "country": "country",
    "Country": "country",
    "currency": "currency",
    "Currency": "currency",
    "price_cny_per_kg": "price_cny_per_kg",
    "price": "price_cny_per_kg",
    "price_source": "price_source",
    "price_observed_at": "price_observed_at",
    "observed_at": "price_observed_at",
    "moq": "moq",
    "MOQ": "moq",
    "pack_size": "pack_size",
    "pack": "pack_size",
    "lead_time_days": "lead_time_days",
    "lead_time": "lead_time_days",
}

_STR_FIELDS = ("name", "url", "product_url", "country", "currency", "price_source", "moq", "pack_size")
_MAX_LEN = {
    "name": 120,
    "url": 1024,
    "product_url": 1024,
    "country": 64,
    "currency": 8,
    "price_source": 32,
    "moq": 64,
    "pack_size": 64,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def norm_supplier_name(name: str) -> str:
    """Stable identity key for a vendor: lower-case, collapse whitespace/punct."""
    s = (name or "").strip().lower()
    s = re.sub(r"[\s\-–—_®™()]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]


def _parse_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
        return float(m.group()) if m else None


def _parse_int(v: Any) -> int | None:
    f = _parse_float(v)
    if f is None:
        return None
    try:
        return int(f)
    except (TypeError, ValueError):
        return None


def _parse_datetime(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo else v
    s = str(v).strip()
    if not s:
        return None
    try:
        # date-only or ISO
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return datetime.fromisoformat(s)
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def is_stale_price(
    *,
    price_cny_per_kg: float | None,
    price_observed_at: datetime | None,
    now: datetime | None = None,
    stale_days: int = STALE_PRICE_DAYS,
) -> bool:
    """True when a price exists but is un-dated or older than ``stale_days``."""
    if price_cny_per_kg is None:
        return False
    if price_observed_at is None:
        return True
    ref = now or _utcnow()
    try:
        age = ref - price_observed_at
    except TypeError:
        return True
    return age > timedelta(days=max(1, int(stale_days)))


def sanitize_supplier_records(
    raw: Iterable[Any] | None,
    *,
    cap: int = MAX_SUPPLIERS_PER_MATERIAL,
) -> list[dict[str, Any]]:
    """Return a cleaned, de-duplicated, capped supplier list.

    Unknown keys are dropped. Empty names are skipped. Dedup key is
    ``(norm_name, product_url or "")``. Commercial fields are optional;
    ``price_source`` defaults to ``manual`` when a price is present.
    """
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        canonical: dict[str, Any] = {
            "name": None,
            "url": None,
            "product_url": None,
            "country": None,
            "currency": None,
            "price_cny_per_kg": None,
            "price_source": None,
            "price_observed_at": None,
            "moq": None,
            "pack_size": None,
            "lead_time_days": None,
        }
        for k, v in item.items():
            target = _KEY_ALIASES.get(str(k))
            if target is None or v is None:
                continue
            if target in _STR_FIELDS:
                text = str(v).strip()
                if not text:
                    continue
                if not canonical[target]:
                    canonical[target] = text[: _MAX_LEN.get(target, 120)]
            elif target == "price_cny_per_kg":
                if canonical[target] is None:
                    canonical[target] = _parse_float(v)
            elif target == "lead_time_days":
                if canonical[target] is None:
                    canonical[target] = _parse_int(v)
            elif target == "price_observed_at":
                if canonical[target] is None:
                    canonical[target] = _parse_datetime(v)
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
        price = canonical["price_cny_per_kg"]
        price_source = canonical["price_source"] or ("manual" if price is not None else None)
        observed = canonical["price_observed_at"]
        # If operator typed a price without a date, stamp today (manual entry).
        if price is not None and observed is None and price_source == "manual":
            observed = _utcnow()
        row: dict[str, Any] = {
            "name": name[:120],
            "url": canonical["url"],
            "product_url": canonical["product_url"],
        }
        if canonical["country"]:
            row["country"] = canonical["country"]
        if price is not None:
            row["price_cny_per_kg"] = price
            row["price_source"] = price_source or "manual"
            row["currency"] = canonical["currency"] or "CNY"
            row["stale_price"] = is_stale_price(
                price_cny_per_kg=price,
                price_observed_at=observed if isinstance(observed, datetime) else None,
            )
        elif canonical["currency"]:
            row["currency"] = canonical["currency"]
        if isinstance(observed, datetime):
            row["price_observed_at"] = observed.isoformat(timespec="seconds")
        if canonical["moq"]:
            row["moq"] = canonical["moq"]
        if canonical["pack_size"]:
            row["pack_size"] = canonical["pack_size"]
        if canonical["lead_time_days"] is not None:
            row["lead_time_days"] = canonical["lead_time_days"]
        out.append(row)
        if len(out) >= max(1, cap):
            break
    return out


def upsert_supplier(session: Session, record: dict[str, Any]) -> SupplierRow:
    """Insert or update a supplier master row keyed by ``norm_name``."""
    name = (record.get("name") or "").strip()
    nk = norm_supplier_name(name)
    row = session.query(SupplierRow).filter(SupplierRow.norm_name == nk).first()
    now = _utcnow()
    url = record.get("url")
    country = record.get("country")
    if isinstance(country, str):
        country = country.strip()[:64] or None
    else:
        country = None
    if row is None:
        row = SupplierRow(
            id=str(uuid.uuid4()),
            norm_name=nk,
            name=name[:200],
            url=(url[:1024] if url else None),
            country=country,
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
    if country and country != (row.country or ""):
        row.country = country
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
    records: list[dict[str, Any]],
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
        price = _parse_float(rec.get("price_cny_per_kg"))
        observed = _parse_datetime(rec.get("price_observed_at"))
        price_source = (rec.get("price_source") or ("manual" if price is not None else "manual"))[
            :32
        ]
        session.add(
            MaterialSupplierRow(
                id=str(uuid.uuid4()),
                material_id=material_id,
                supplier_id=supplier.id,
                product_url=product_url,
                source=(source or "app")[:32],
                currency=(str(rec.get("currency") or "").strip()[:8] or None),
                price_cny_per_kg=price,
                price_source=price_source or "manual",
                price_observed_at=observed,
                moq=(str(rec.get("moq") or "").strip()[:64] or None),
                pack_size=(str(rec.get("pack_size") or "").strip()[:64] or None),
                lead_time_days=_parse_int(rec.get("lead_time_days")),
                created_at=now,
            )
        )
        count += 1
    session.flush()
    return count


def _link_to_dict(link: MaterialSupplierRow, supplier: SupplierRow) -> dict[str, Any]:
    observed = link.price_observed_at
    price = link.price_cny_per_kg
    row: dict[str, Any] = {
        "name": supplier.name,
        "url": supplier.url,
        "product_url": link.product_url or None,
    }
    if supplier.country:
        row["country"] = supplier.country
    if link.currency:
        row["currency"] = link.currency
    if price is not None:
        row["price_cny_per_kg"] = price
        row["price_source"] = link.price_source or "manual"
        row["stale_price"] = is_stale_price(
            price_cny_per_kg=price, price_observed_at=observed
        )
    if observed is not None:
        row["price_observed_at"] = observed.isoformat(timespec="seconds")
    if link.moq:
        row["moq"] = link.moq
    if link.pack_size:
        row["pack_size"] = link.pack_size
    if link.lead_time_days is not None:
        row["lead_time_days"] = link.lead_time_days
    return row


def fetch_suppliers_for_materials(
    session: Session, material_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
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
    out: dict[str, list[dict[str, Any]]] = {mid: [] for mid in material_ids}
    for link, supplier in rows:
        out.setdefault(link.material_id, []).append(_link_to_dict(link, supplier))
    return out


def sync_material_suppliers_from_json(
    session: Session,
    material_id: str,
    raw: Any,
    *,
    source: str = "app",
    clear_json_projection: bool = False,
) -> list[dict[str, Any]]:
    """Sanitize ``raw``, write link tables, return projection for dual-write."""
    cleaned = sanitize_supplier_records(raw)
    replace_material_suppliers(session, material_id, cleaned, source=source)
    if clear_json_projection:
        return []
    # Re-read from DB so projection matches stored types / stale flags.
    return fetch_suppliers_for_materials(session, [material_id]).get(material_id, cleaned)
