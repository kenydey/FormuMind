"""Chemical-data enrichment via PubChemPy (optional, with offline fallback).

When ``pubchempy`` is installed and the platform has network access, this
service fills in missing ``smiles`` / ``molar_mass`` fields on the raw-material
library by querying PubChem's PUG-REST API by compound name. Enriched SMILES
feed the RDKit descriptor path in ``predictor`` and the formula checks in
``chemistry``. When the library is absent, enrichment is a no-op and the
hand-curated values in ``knowledge.RAW_MATERIALS`` are used unchanged.

Enrichment is opt-in (``FORMUMIND_ENRICH_COMPOUNDS=true``) and never runs during
tests, so offline behaviour stays deterministic.
"""
from __future__ import annotations

import re
import time
from typing import Any

_LOOKUP_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 86400  # 24h


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _LOOKUP_CACHE.get(key.lower().strip())
    if not entry:
        return None
    ts, data = entry
    if time.time() - ts > _CACHE_TTL_SEC:
        _LOOKUP_CACHE.pop(key.lower().strip(), None)
        return None
    return data


def _cache_set(key: str, data: dict[str, Any]) -> None:
    _LOOKUP_CACHE[key.lower().strip()] = (time.time(), data)


def _pubchempy_available() -> bool:
    try:
        import pubchempy  # noqa: F401

        return True
    except Exception:
        return False


def _extract_zh_name(synonyms: list[str]) -> str | None:
    for s in synonyms:
        if any("\u4e00" <= c <= "\u9fff" for c in s):
            return s
    return None


def _local_lookup(q: str) -> dict[str, Any] | None:
    from ..domain.knowledge import RAW_MATERIALS

    key = q.strip()
    spec = RAW_MATERIALS.get(key)
    if not spec:
        for name, props in RAW_MATERIALS.items():
            if key.lower() in name.lower():
                spec = props
                key = name
                break
    if not spec:
        return None
    return {
        "query": q,
        "cas": spec.get("cas_no") or "",
        "iupac_name": key,
        "zh_name": spec.get("zh_name") or "",
        "formula": spec.get("formula") or "",
        "smiles": spec.get("smiles") or "",
        "molar_mass": spec.get("molar_mass"),
    }


def _compound_to_result(q: str, c: Any, synonyms: list[str] | None = None) -> dict[str, Any]:
    syns = synonyms or []
    cas = ""
    for s in syns:
        if re.fullmatch(r"\d{2,7}-\d{2}-\d", s):
            cas = s
            break
    smiles = getattr(c, "canonical_smiles", None) or getattr(c, "isomeric_smiles", None)
    formula = getattr(c, "molecular_formula", None) or ""
    iupac = getattr(c, "iupac_name", None) or getattr(c, "synonyms", [""])[0] if hasattr(c, "synonyms") else q
    zh = _extract_zh_name(syns)
    out: dict[str, Any] = {
        "query": q,
        "cas": cas,
        "iupac_name": iupac or q,
        "zh_name": zh or "",
        "formula": formula,
    }
    if smiles:
        out["smiles"] = smiles
    mw = getattr(c, "molecular_weight", None)
    if mw is not None:
        out["molar_mass"] = float(mw)
    return out


def lookup(name: str) -> dict | None:
    """Return {smiles, molar_mass, xlogp} for a compound name, or None.

    Best-effort: any network/parse error returns None so callers fall back to
    the curated value.
    """
    try:  # pragma: no cover - requires pubchempy + network
        import pubchempy as pcp

        matches = pcp.get_compounds(name, "name")
        if not matches:
            return None
        c = matches[0]
        syns = pcp.get_synonyms(c.cid, "cid") if c.cid else []
        out: dict = {}
        smiles = getattr(c, "canonical_smiles", None) or getattr(c, "isomeric_smiles", None)
        if smiles:
            out["smiles"] = smiles
        if getattr(c, "molecular_weight", None):
            out["molar_mass"] = float(c.molecular_weight)
        if getattr(c, "xlogp", None) is not None:
            out["xlogp"] = float(c.xlogp)
        for s in syns:
            if re.fullmatch(r"\d{2,7}-\d{2}-\d", s):
                out["cas_no"] = s
                break
        return out or None
    except Exception:
        return None


def lookup_compound(q: str) -> dict[str, Any]:
    """Lookup by Chinese/English name or CAS No. Returns best-effort match dict."""
    q = q.strip()
    if not q:
        return {"query": q, "cas": "", "iupac_name": "", "zh_name": "", "formula": ""}

    cached = _cache_get(q)
    if cached:
        return cached

    local = _local_lookup(q)
    if local and (local.get("cas") or local.get("formula")):
        _cache_set(q, local)
        return local

    try:  # pragma: no cover - network path
        import pubchempy as pcp

        if re.fullmatch(r"\d{2,7}-\d{2}-\d", q):
            matches = pcp.get_compounds(q, "name")
            if not matches:
                matches = pcp.get_compounds(q.replace("-", ""), "name")
        else:
            matches = pcp.get_compounds(q, "name")
        if matches:
            c = matches[0]
            syns = pcp.get_synonyms(c.cid, "cid") if c.cid else []
            result = _compound_to_result(q, c, syns)
            _cache_set(q, result)
            return result
    except Exception:
        pass

    fallback = {
        "query": q,
        "cas": q if re.fullmatch(r"\d{2,7}-\d{2}-\d", q) else "",
        "iupac_name": q,
        "zh_name": q if any("\u4e00" <= c <= "\u9fff" for c in q) else "",
        "formula": "",
    }
    _cache_set(q, fallback)
    return fallback


def enrich_materials(materials: dict[str, dict]) -> int:
    """Fill missing ``smiles`` / ``molar_mass`` fields in-place.

    Returns the number of materials updated. No-op (returns 0) when pubchempy
    is unavailable. Only blank fields are touched — curated values win.
    """
    if not _pubchempy_available():
        return 0
    updated = 0
    for name, spec in materials.items():
        if spec.get("smiles") and spec.get("molar_mass"):
            continue
        data = lookup(name)  # pragma: no cover - network path
        if not data:
            continue
        if not spec.get("smiles") and data.get("smiles"):
            spec["smiles"] = data["smiles"]
            updated += 1
        if not spec.get("molar_mass") and data.get("molar_mass"):
            spec["molar_mass"] = round(data["molar_mass"], 2)
    return updated
