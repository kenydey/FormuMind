"""Literature / KG / KB-product alternatives for material substitution (L2).

Advisory only — no formula Δ. Failures degrade to an empty list.
"""
from __future__ import annotations

import re
from typing import Any

from loguru import logger

from ..db.material_store import norm_key
from ..domain.knowledge import RAW_MATERIALS

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def _match_catalog(cas: str, smiles: str, name: str) -> tuple[bool, str | None]:
    cas_n = (cas or "").strip()
    smiles_n = (smiles or "").strip()
    name_n = norm_key(name or "")
    for cat_name, spec in RAW_MATERIALS.items():
        if cas_n and str(spec.get("cas_no") or "").strip() == cas_n:
            return True, cat_name
        if smiles_n and str(spec.get("smiles") or "").strip() == smiles_n:
            return True, cat_name
        if name_n and norm_key(cat_name) == name_n:
            return True, cat_name
    return False, None


def _catalog_entity_id(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").lower())[:80]
    return f"chem:catalog:{slug}"


def _evidence_from_path(path: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in path or []:
        rel = getattr(step, "relation", None)
        if rel is None:
            continue
        for ev in getattr(rel, "evidence", None) or []:
            out.append(
                {
                    "source_id": getattr(ev, "source_id", None) or "",
                    "chunk_id": getattr(ev, "chunk_id", None),
                    "sentence": getattr(ev, "sentence", None) or "",
                    "confidence": getattr(ev, "confidence", None),
                }
            )
            if len(out) >= 5:
                return out
    return out


def _from_kg(material: str, *, limit: int) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from .kg.retrieval import kg_enabled, resolve_query
        from .kg.graph_query import discover_substitutes
    except Exception as exc:
        return [], f"kg_import_failed:{exc}"

    try:
        if not kg_enabled():
            return [], "kg_disabled"
    except Exception:
        return [], "kg_disabled"

    entity_id = None
    try:
        resolved = resolve_query(material)
        if resolved.chemicals:
            entity_id = resolved.chemicals[0].id
        elif resolved.trade_products:
            entity_id = resolved.trade_products[0].id
    except Exception as exc:
        logger.debug("literature kg resolve failed ({})", exc)

    if not entity_id:
        entity_id = _catalog_entity_id(material)

    try:
        resp = discover_substitutes(entity_id, limit=limit)
    except Exception as exc:
        logger.debug("literature kg discover failed ({})", exc)
        return [], f"kg_discover_failed:{exc}"

    rows: list[dict[str, Any]] = []
    for cand in resp.substitutes or []:
        name = (cand.entity_name or "").strip()
        if not name or norm_key(name) == norm_key(material):
            continue
        in_cat, cat_name = _match_catalog("", "", name)
        rows.append(
            {
                "name": name,
                "source": "kg",
                "confidence": float(cand.confidence or 0.0),
                "entity_id": cand.entity_id,
                "cas_no": None,
                "smiles": None,
                "role_hint": None,
                "in_catalog": in_cat,
                "catalog_name": cat_name,
                "evidence": _evidence_from_path(cand.path),
                "note": "知识图谱 substitutes 边",
            }
        )
        if len(rows) >= limit:
            break
    return rows, None


def _from_kb_products(
    material: str,
    *,
    cas: str = "",
    role_hint: str | None = None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from ..db.product_store import get_product_store

        store = get_product_store()
        products = store.find_for_material(material, cas=cas or "")
        if len(products) < limit and role_hint:
            extra = store.search(role_hint, limit=limit)
            seen = {getattr(p, "id", id(p)) for p in products}
            for p in extra:
                pid = getattr(p, "id", id(p))
                if pid in seen:
                    continue
                products.append(p)
                seen.add(pid)
                if len(products) >= limit * 2:
                    break
    except Exception as exc:
        logger.debug("literature kb_products failed ({})", exc)
        return [], f"kb_products_unavailable:{exc}"

    rows: list[dict[str, Any]] = []
    for p in products:
        trade = (getattr(p, "trade_name", None) or "").strip()
        generic = (getattr(p, "generic_name", None) or "").strip()
        name = generic or trade
        if not name or norm_key(name) == norm_key(material):
            continue
        cas_no = (getattr(p, "cas", None) or "").strip() or None
        if cas_no and not _CAS_RE.match(cas_no):
            cas_no = None
        smiles = getattr(p, "smiles", None) or None
        in_cat, cat_name = _match_catalog(cas_no or "", str(smiles or ""), name)
        mentions = int(getattr(p, "mention_count", 0) or 0)
        rows.append(
            {
                "name": name,
                "source": "kb_product",
                "confidence": min(0.95, 0.4 + 0.05 * mentions),
                "entity_id": None,
                "cas_no": cas_no,
                "smiles": str(smiles).strip() if smiles else None,
                "role_hint": getattr(p, "role", None) or role_hint,
                "in_catalog": in_cat,
                "catalog_name": cat_name,
                "evidence": [],
                "note": f"KB 产品登记簿 · mentions={mentions}",
            }
        )
        if len(rows) >= limit:
            break
    return rows, None


def fetch_literature_alternatives(
    *,
    material: str,
    cas_no: str = "",
    role_hint: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Aggregate KG + kb_products into advisory literature substitutes."""
    lim = max(1, min(25, int(limit)))
    display = (material or "").strip()
    if not display:
        return {
            "literature": [],
            "literature_meta": {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": "empty_material",
                "providers": [],
            },
        }

    providers: list[str] = []
    reasons: list[str] = []
    merged: list[dict[str, Any]] = []
    seen: set[str] = {norm_key(display)}

    kg_rows, kg_reason = _from_kg(display, limit=lim)
    if kg_reason:
        reasons.append(kg_reason)
    elif kg_rows:
        providers.append("kg")
    for row in kg_rows:
        key = norm_key(row["name"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)

    need = lim - len(merged)
    if need > 0:
        kb_rows, kb_reason = _from_kb_products(
            display, cas=cas_no or "", role_hint=role_hint, limit=need
        )
        if kb_reason:
            reasons.append(kb_reason)
        elif kb_rows:
            providers.append("kb_product")
        for row in kb_rows:
            key = norm_key(row["name"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= lim:
                break

    merged.sort(key=lambda r: (-float(r.get("confidence") or 0.0), r.get("name") or ""))
    skipped = None
    if not merged and reasons:
        skipped = "; ".join(reasons[:3])
    elif not merged:
        skipped = "无文献/产品替代命中"

    return {
        "literature": merged[:lim],
        "literature_meta": {
            "enabled": True,
            "queried": True,
            "count": len(merged[:lim]),
            "skipped_reason": skipped,
            "providers": providers,
        },
    }
