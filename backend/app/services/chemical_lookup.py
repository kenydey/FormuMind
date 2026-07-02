"""Chemical name / CAS lookup via PubChem PUG REST with catalog fallback and 24h cache."""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

from ..domain.knowledge import RAW_MATERIALS

_CJK_RE = re.compile(r"[一-鿿]")
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SEC = 86400


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, payload = entry
    if time.time() - ts > _TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    _CACHE[key] = (time.time(), payload)
    return payload


def _zh_from_query(q: str) -> str:
    parts = _CJK_RE.findall(q)
    return "".join(parts) if parts else ""


def _lookup_catalog(q: str) -> dict[str, Any] | None:
    key = q.strip()
    spec = RAW_MATERIALS.get(key)
    if not spec:
        for name, row in RAW_MATERIALS.items():
            if name.lower() == key.lower():
                spec = row
                key = name
                break
    if not spec:
        return None
    return {
        "query": q,
        "cas": spec.get("cas_no") or "",
        "iupac_name": key,
        "zh_name": spec.get("zh_name") or _zh_from_query(q),
        "formula": spec.get("formula") or "",
        "smiles": spec.get("smiles"),
        "molar_mass": spec.get("molar_mass"),
        "found": True,
        "source": "catalog",
    }


def _lookup_pubchem(q: str) -> dict[str, Any] | None:
    try:
        import httpx
    except ImportError:
        return None
    encoded = quote(q.strip(), safe="")
    props_url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}"
        "/property/MolecularFormula,MolecularWeight,IUPACName/JSON"
    )
    cas_url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}"
        "/xrefs/RegistryNumber/JSON"
    )
    try:
        with httpx.Client(timeout=12.0) as client:
            props_resp = client.get(props_url)
            if props_resp.status_code != 200:
                return None
            props_data = props_resp.json()
            props = (props_data.get("PropertyTable") or {}).get("Properties") or []
            row = props[0] if props else {}
            cas = ""
            cas_resp = client.get(cas_url)
            if cas_resp.status_code == 200:
                cas_data = cas_resp.json()
                nums = (
                    (cas_data.get("InformationList") or {})
                    .get("Information", [{}])[0]
                    .get("RegistryNumber", [])
                )
                if nums:
                    cas = str(nums[0])
            return {
                "query": q,
                "cas": cas,
                "iupac_name": row.get("IUPACName") or q,
                "zh_name": _zh_from_query(q),
                "formula": row.get("MolecularFormula") or "",
                "smiles": None,
                "molar_mass": row.get("MolecularWeight"),
                "found": True,
                "source": "pubchem",
            }
    except Exception:
        return None


def _lookup_offline_compounds(q: str) -> dict[str, Any] | None:
    from .compounds import lookup

    data = lookup(q)
    if not data:
        return None
    return {
        "query": q,
        "cas": "",
        "iupac_name": q,
        "zh_name": _zh_from_query(q),
        "formula": "",
        "smiles": data.get("smiles"),
        "molar_mass": data.get("molar_mass"),
        "found": True,
        "source": "pubchempy",
    }


def lookup_chemical(q: str) -> dict[str, Any]:
    """Resolve a chemical query (CN/EN name or CAS) to structured metadata."""
    key = (q or "").strip().lower()
    if not key:
        return {
            "query": q,
            "cas": "",
            "iupac_name": "",
            "zh_name": "",
            "formula": "",
            "smiles": None,
            "molar_mass": None,
            "found": False,
            "source": "empty",
        }
    cached = _cache_get(key)
    if cached:
        return cached
    for resolver in (_lookup_catalog, _lookup_pubchem, _lookup_offline_compounds):
        hit = resolver(q.strip())
        if hit:
            return _cache_put(key, hit)
    empty = {
        "query": q,
        "cas": "",
        "iupac_name": q.strip(),
        "zh_name": _zh_from_query(q),
        "formula": "",
        "smiles": None,
        "molar_mass": None,
        "found": False,
        "source": "none",
    }
    return _cache_put(key, empty)
