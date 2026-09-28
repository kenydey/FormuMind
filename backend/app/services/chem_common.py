"""Shared primitives for chemistry lookup services.

Extracted duplicate logic (code review 2026-09-28): the TTL-cache get/put
helpers copied across ``chemical_lookup`` / ``external_alternatives`` /
``surechembl_client``, and the catalog matcher copied verbatim between
``external_alternatives`` and ``literature_alternatives``.

Each service keeps its own module-level ``_CACHE`` dict and ``_TTL_SEC``
(tests poke at ``chemical_lookup._CACHE`` directly, and TTLs differ per
service), so the shared helpers take the store as an explicit argument and
the per-module ``_cache_get`` / ``_cache_put`` thin wrappers resolve the
module-global ``_CACHE`` at call time.
"""

from __future__ import annotations

import time
from typing import Any, TypeVar

from ..db.material_store import norm_key
from ..domain.knowledge import RAW_MATERIALS

PayloadT = TypeVar("PayloadT")


def cache_get(
    store: dict[str, tuple[float, Any]], key: str, ttl_sec: float
) -> Any | None:
    """Return the cached payload, or None on miss/expiry (expired entries evicted)."""
    entry = store.get(key)
    if not entry:
        return None
    ts, payload = entry
    if time.time() - ts > ttl_sec:
        store.pop(key, None)
        return None
    return payload


def cache_put(
    store: dict[str, tuple[float, PayloadT]], key: str, payload: PayloadT
) -> PayloadT:
    """Store ``payload`` with a now timestamp; returns the payload."""
    store[key] = (time.time(), payload)
    return payload


def _match_catalog(cas: str, smiles: str, name: str) -> tuple[bool, str | None]:
    """Match (cas, smiles, name) against ``RAW_MATERIALS`` catalog entries."""
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
