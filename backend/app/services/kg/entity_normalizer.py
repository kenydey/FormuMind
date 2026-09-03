"""Normalize vision-extracted surface forms to KG entity IDs."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ...config import Settings, get_settings
from ...db.entity_store import get_entity_store
from ...domain.knowledge import RAW_MATERIALS, TRADE_ALIASES, resolve_material_name

_CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")
_METRIC_ALIASES: dict[str, str] = {
    "nss": "salt_spray_nss",
    "salt spray": "salt_spray_nss",
    "盐雾": "salt_spray_nss",
    "neutral salt spray": "salt_spray_nss",
    "附着力": "adhesion",
    "adhesion": "adhesion",
    "铅笔硬度": "pencil_hardness",
    "pencil hardness": "pencil_hardness",
    "光泽": "gloss",
    "gloss": "gloss",
}


@dataclass(frozen=True)
class ResolvedEntity:
    entity_id: str
    kind: str
    canonical_name: str
    confidence: float
    composition_status: str = "unknown"
    zh_name: str = ""
    cas_no: str | None = None
    linked_catalog_key: str | None = None


def _catalog_entity_id(catalog_key: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", catalog_key.lower())[:80]
    return f"chem:catalog:{safe}"


def _trade_entity_id(norm_key: str) -> str:
    return f"tp:{norm_key}"


def _surface_entity_id(name: str) -> str:
    digest = hashlib.sha1(name.strip().lower().encode()).hexdigest()[:12]
    return f"chem:surface:{digest}"


def _metric_entity_id(metric: str) -> tuple[str, str]:
    key = (metric or "").strip().lower()
    normalized = _METRIC_ALIASES.get(key)
    if not normalized:
        normalized = re.sub(r"[^a-z0-9]+", "_", key)[:48] or "unknown_metric"
    return f"metric:{normalized}", metric.strip() or normalized


def _substrate_entity_id(name: str) -> tuple[str, str]:
    clean = (name or "").strip() or "unknown_substrate"
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", clean.lower())[:60]
    return f"substrate:{slug}", clean


def formulation_entity_id(source_id: str, example_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", (example_id or "example").lower())[:48]
    sid = re.sub(r"[^a-zA-Z0-9-]+", "", (source_id or "local")[:16])
    return f"form:{sid}:{slug}"


def resolve_material_surface(
    name: str,
    *,
    settings: Settings | None = None,
) -> ResolvedEntity | None:
    settings = settings or get_settings()
    text = (name or "").strip()
    if len(text) < 2:
        return None

    for cas in _CAS_RE.findall(text):
        eid = f"chem:cas:{cas}"
        row = get_entity_store().get_entity(eid)
        if row:
            return ResolvedEntity(
                entity_id=eid,
                kind=row.kind,
                canonical_name=row.canonical_name,
                confidence=0.95,
                composition_status=row.composition_status or "resolved",
                zh_name=row.zh_name or "",
                cas_no=row.cas_no,
            )
        return ResolvedEntity(
            entity_id=eid,
            kind="chemical",
            canonical_name=cas,
            confidence=0.92,
            composition_status="resolved",
            cas_no=cas,
        )

    catalog = resolve_material_name(text)
    if catalog in RAW_MATERIALS:
        spec = RAW_MATERIALS[catalog]
        return ResolvedEntity(
            entity_id=_catalog_entity_id(catalog),
            kind="chemical",
            canonical_name=catalog,
            confidence=0.88,
            composition_status="resolved",
            zh_name=spec.get("zh_name") or "",
            cas_no=spec.get("cas_no"),
            linked_catalog_key=catalog,
        )

    if text.lower() in TRADE_ALIASES or re.match(r"^[A-Za-z]{2,}[- ]?\d{2,4}$", text):
        from ...db.product_store import norm_key

        nk = norm_key(text, "")
        eid = _trade_entity_id(nk)
        row = get_entity_store().get_entity(eid)
        if row:
            return ResolvedEntity(
                entity_id=eid,
                kind="trade_product",
                canonical_name=row.canonical_name,
                confidence=0.82,
                composition_status=row.composition_status or "unknown",
            )
        return ResolvedEntity(
            entity_id=eid,
            kind="trade_product",
            canonical_name=text,
            confidence=0.65,
            composition_status="unknown",
        )

    for row in get_entity_store().search_entities(text, limit=3):
        if row.kind in ("chemical", "trade_product", "element"):
            return ResolvedEntity(
                entity_id=row.id,
                kind=row.kind,
                canonical_name=row.canonical_name,
                confidence=0.75,
                composition_status=row.composition_status or "unknown",
                zh_name=row.zh_name or "",
                cas_no=row.cas_no,
                linked_catalog_key=row.linked_catalog_key,
            )

    return ResolvedEntity(
        entity_id=_surface_entity_id(text),
        kind="chemical",
        canonical_name=text,
        confidence=0.55,
        composition_status="partial",
        zh_name=text if any("\u4e00" <= c <= "\u9fff" for c in text) else "",
    )


def resolve_metric_surface(metric: str) -> ResolvedEntity:
    eid, display = _metric_entity_id(metric)
    return ResolvedEntity(
        entity_id=eid,
        kind="metric",
        canonical_name=display,
        confidence=0.9,
        composition_status="resolved",
    )


def resolve_substrate_surface(name: str) -> ResolvedEntity:
    eid, display = _substrate_entity_id(name)
    return ResolvedEntity(
        entity_id=eid,
        kind="substrate",
        canonical_name=display,
        confidence=0.85,
        composition_status="resolved",
    )


def resolved_to_upsert_fields(resolved: ResolvedEntity) -> dict:
    fields: dict = {
        "id": resolved.entity_id,
        "kind": resolved.kind,
        "canonical_name": resolved.canonical_name,
        "composition_status": resolved.composition_status,
    }
    if resolved.zh_name:
        fields["zh_name"] = resolved.zh_name
    if resolved.cas_no:
        fields["cas_no"] = resolved.cas_no
    if resolved.linked_catalog_key:
        fields["linked_catalog_key"] = resolved.linked_catalog_key
    return fields
