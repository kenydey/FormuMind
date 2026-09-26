"""Collect supply risk flags from material suppliers (post-A′ #1).

Reads ``suppliers_json`` / hydrated ``material_suppliers`` for formulation
ingredients and substitution candidates. Display + light ranking only —
does not invent prices or crawl vendors.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from .supplier_normalize import STALE_PRICE_DAYS, is_stale_price, sanitize_supplier_records

logger = logging.getLogger(__name__)

# Lead times at or above this are flagged (days). Soft signal only.
LONG_LEAD_TIME_DAYS = 45
# Soft demotion applied to structural/requirement ranking when stale (not a hard cut).
STALE_SCORE_PENALTY = 0.03


def _suppliers_for_name(name: str) -> list[dict[str, Any]]:
    """Best-effort supplier rows for a material display name."""
    name = (name or "").strip()
    if not name:
        return []
    try:
        from ..db.material_store import get_material_store

        row = get_material_store().get(name)
    except Exception as exc:
        logger.debug("supply lookup failed for %s: %s", name, exc)
        return []
    if row is None:
        return []
    raw = getattr(row, "suppliers_json", None)
    if not raw:
        return []
    try:
        return sanitize_supplier_records(raw)
    except Exception:
        return list(raw) if isinstance(raw, list) else []


def annotate_supply(suppliers: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    """Summarize commercial risk for one material's supplier list."""
    rows = list(suppliers or [])
    if not rows:
        return {
            "has_suppliers": False,
            "missing_price": False,
            "stale_price": False,
            "long_lead_time": False,
            "max_lead_time_days": None,
            "badges": [],
            "flags": [],
        }

    any_price = False
    any_stale = False
    any_fresh_price = False
    max_lead: int | None = None
    for s in rows:
        if not isinstance(s, dict):
            continue
        price = s.get("price_cny_per_kg")
        if price is not None:
            any_price = True
            # Prefer explicit stale_price annotation from normalize; recompute fallback.
            if s.get("stale_price") is True:
                any_stale = True
            else:
                from datetime import datetime

                observed = s.get("price_observed_at")
                obs_dt = None
                if isinstance(observed, datetime):
                    obs_dt = observed
                elif isinstance(observed, str) and observed.strip():
                    try:
                        obs_dt = datetime.fromisoformat(observed.replace("Z", "+00:00")).replace(
                            tzinfo=None
                        )
                    except ValueError:
                        obs_dt = None
                if is_stale_price(
                    price_cny_per_kg=float(price),
                    price_observed_at=obs_dt,
                    stale_days=STALE_PRICE_DAYS,
                ):
                    any_stale = True
                else:
                    any_fresh_price = True
        lead = s.get("lead_time_days")
        if lead is not None:
            try:
                ld = int(lead)
            except (TypeError, ValueError):
                continue
            max_lead = ld if max_lead is None else max(max_lead, ld)

    missing_price = not any_price
    # stale only counts when we have at least one priced row that is stale
    # and no fresh priced row as counter-evidence for "all stale".
    stale = bool(any_stale and not any_fresh_price)
    long_lead = bool(max_lead is not None and max_lead >= LONG_LEAD_TIME_DAYS)

    badges: list[str] = []
    flags: list[str] = []
    if missing_price:
        badges.append("missing_price")
        flags.append("缺价")
    if stale:
        badges.append("stale_price")
        flags.append("stale_price")
    if long_lead:
        badges.append("long_lead_time")
        flags.append(f"交期{max_lead}天")

    return {
        "has_suppliers": True,
        "missing_price": missing_price,
        "stale_price": stale,
        "long_lead_time": long_lead,
        "max_lead_time_days": max_lead,
        "badges": badges,
        "flags": flags,
    }


def supply_annotation_for_material(name: str) -> dict[str, Any]:
    return annotate_supply(_suppliers_for_name(name))


def collect_formulation_supply_flags(
    ingredient_names: Iterable[str],
    *,
    existing_warnings: Iterable[str] | None = None,
) -> list[str]:
    """Build human-readable supply_flags for FormulationExplain."""
    flags: list[str] = []
    seen: set[str] = set()

    for w in existing_warnings or []:
        low = str(w).lower()
        if any(k in low for k in ("供应", "缺货", "交期", "discontinued", "restricted", "supply", "stale")):
            s = str(w)
            if s not in seen:
                seen.add(s)
                flags.append(s)

    for name in ingredient_names:
        ann = supply_annotation_for_material(name)
        if not ann.get("has_suppliers") and not ann.get("flags"):
            continue
        for f in ann.get("flags") or []:
            label = f"{name}: {f}"
            if label not in seen:
                seen.add(label)
                flags.append(label)
    return flags[:12]
