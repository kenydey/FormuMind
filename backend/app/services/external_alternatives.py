"""External (networked) alternative materials via PubChem similarity.

Used by material substitution to supplement the local catalog pool. Failures
degrade to an empty list — never raise into the substitutes API path.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import quote

from loguru import logger

from ..domain.knowledge import RAW_MATERIALS
from ..db.material_store import norm_key
from .errors import degrade_return

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_TTL_SEC = 86400
_PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"


def external_substitutes_enabled() -> bool:
    try:
        from ..config import get_settings

        return bool(get_settings().external_substitutes)
    except Exception:
        raw = (os.environ.get("FORMUMIND_EXTERNAL_SUBSTITUTES") or "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}


def _cache_get(key: str) -> list[dict[str, Any]] | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, payload = entry
    if time.time() - ts > _TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _CACHE[key] = (time.time(), payload)
    return payload


def clear_external_cache() -> None:
    _CACHE.clear()


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


def resolve_slot_identity(
    material: str,
    spec: dict[str, Any] | None = None,
    *,
    network: bool = True,
) -> dict[str, Any]:
    """Resolve CAS / SMILES for a formulation slot material.

    ``network=False`` stays catalog-only (used when external search is off).
    """
    display = (material or "").strip()
    row = dict(spec or {})
    cas = str(row.get("cas_no") or "").strip()
    smiles = str(row.get("smiles") or "").strip()
    source = "catalog" if (cas or smiles) else "none"

    if network and (not cas or not smiles):
        try:
            from .chemical_lookup import lookup_chemical

            hit = lookup_chemical(display) or {}
        except Exception as exc:
            hit = degrade_return(logger, exc, "identity lookup failed", {}) or {}
        if hit.get("found"):
            if not cas and hit.get("cas"):
                cas = str(hit.get("cas") or "").strip()
            if not smiles and hit.get("smiles"):
                smiles = str(hit.get("smiles") or "").strip()
            source = str(hit.get("source") or source)

    return {
        "query": display,
        "cas_no": cas,
        "smiles": smiles or None,
        "cid": None,
        "source": source if (cas or smiles) else "none",
        "resolved": bool(cas or smiles),
    }


def _http_get_json(url: str, *, timeout: float = 12.0) -> dict[str, Any] | None:
    try:
        import httpx
    except ImportError:
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as exc:
        return degrade_return(logger, exc, "pubchem GET failed", None)


def _cid_from_smiles(smiles: str) -> int | None:
    encoded = quote(smiles.strip(), safe="")
    data = _http_get_json(f"{_PUBCHEM}/compound/smiles/{encoded}/cids/JSON")
    if not data:
        return None
    cids = (data.get("IdentifierList") or {}).get("CID") or []
    return int(cids[0]) if cids else None


def _cid_from_name(name: str) -> int | None:
    encoded = quote(name.strip(), safe="")
    data = _http_get_json(f"{_PUBCHEM}/compound/name/{encoded}/cids/JSON")
    if not data:
        return None
    cids = (data.get("IdentifierList") or {}).get("CID") or []
    return int(cids[0]) if cids else None


def _cas_map_for_cids(cids: list[int]) -> dict[int, str]:
    """Batch RegistryNumber lookup; best-effort."""
    out: dict[int, str] = {}
    if not cids:
        return out
    # PubChem accepts comma-separated CIDs.
    chunk = ",".join(str(c) for c in cids[:25])
    data = _http_get_json(f"{_PUBCHEM}/compound/cid/{chunk}/xrefs/RegistryNumber/JSON")
    if not data:
        return out
    for info in (data.get("InformationList") or {}).get("Information") or []:
        try:
            cid_i = int(info.get("CID"))
        except (TypeError, ValueError):
            continue
        nums = info.get("RegistryNumber") or []
        chosen = ""
        for n in nums:
            s = str(n).strip()
            if _CAS_RE.match(s):
                chosen = s
                break
        if not chosen and nums:
            chosen = str(nums[0]).strip()
        if chosen:
            out[cid_i] = chosen
    return out


def pubchem_similar_by_smiles(
    smiles: str,
    *,
    threshold: int = 85,
    limit: int = 8,
    exclude_cid: int | None = None,
) -> list[dict[str, Any]]:
    """PubChem fastsimilarity_2d → property rows."""
    smiles = (smiles or "").strip()
    if not smiles:
        return []
    thr = max(60, min(100, int(threshold)))
    lim = max(1, min(25, int(limit)))
    cache_key = f"sim:{smiles}:{thr}:{lim}:{exclude_cid or 0}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return list(cached)

    encoded = quote(smiles, safe="")
    # Request a few extras so we can drop the query compound itself.
    max_records = min(25, lim + 3)
    url = (
        f"{_PUBCHEM}/compound/fastsimilarity_2d/smiles/{encoded}/property/"
        f"Title,IUPACName,MolecularFormula,MolecularWeight,CanonicalSMILES,ConnectivitySMILES"
        f"/JSON?Threshold={thr}&MaxRecords={max_records}"
    )
    data = _http_get_json(url, timeout=18.0)
    if not data:
        return _cache_put(cache_key, [])

    props = (data.get("PropertyTable") or {}).get("Properties") or []
    prelim: list[dict[str, Any]] = []
    for row in props:
        cid = row.get("CID")
        if cid is None:
            continue
        try:
            cid_i = int(cid)
        except (TypeError, ValueError):
            continue
        if exclude_cid is not None and cid_i == exclude_cid:
            continue
        smi = (
            str(row.get("CanonicalSMILES") or row.get("ConnectivitySMILES") or "").strip()
            or None
        )
        title = str(row.get("Title") or row.get("IUPACName") or f"CID:{cid_i}").strip()
        iupac = str(row.get("IUPACName") or "").strip() or None
        # Similarity score is not always returned by this endpoint; use threshold
        # as a lower-bound proxy when absent.
        sim_raw = row.get("Similarity") or row.get("Score")
        try:
            similarity = float(sim_raw) / (100.0 if float(sim_raw) > 1.0 else 1.0)
        except (TypeError, ValueError):
            similarity = thr / 100.0
        prelim.append(
            {
                "name": title,
                "iupac_name": iupac,
                "cas_no": None,
                "smiles": smi,
                "cid": cid_i,
                "formula": str(row.get("MolecularFormula") or "") or None,
                "molar_mass": row.get("MolecularWeight"),
                "similarity": round(similarity, 4),
                "source": "pubchem_similar",
                "in_catalog": False,
                "catalog_name": None,
                "role_hint": None,
                "note": "结构相似；未做配方 Δ 预测（入库后可再算）",
            }
        )
        if len(prelim) >= max_records:
            break

    cas_map = _cas_map_for_cids([int(r["cid"]) for r in prelim])
    out: list[dict[str, Any]] = []
    for row in prelim:
        cas = cas_map.get(int(row["cid"]), "")
        row["cas_no"] = cas or None
        in_cat, cat_name = _match_catalog(cas, row.get("smiles") or "", row["name"])
        row["in_catalog"] = in_cat
        row["catalog_name"] = cat_name
        out.append(row)

    # Drop the query structure itself when CanonicalSMILES matches.
    filtered = [r for r in out if (r.get("smiles") or "") != smiles]
    out = (filtered or out)[:lim]

    return _cache_put(cache_key, out)


def fetch_external_alternatives(
    *,
    material: str,
    spec: dict[str, Any] | None = None,
    limit: int = 8,
    threshold: int = 85,
    role_hint: str | None = None,
) -> dict[str, Any]:
    """Resolve identity and (when possible) return PubChem-similar externals."""
    if not external_substitutes_enabled():
        return {
            "identity": {
                "query": material,
                "cas_no": "",
                "smiles": None,
                "cid": None,
                "source": "disabled",
                "resolved": False,
            },
            "external": [],
            "external_meta": {
                "enabled": False,
                "queried": False,
                "count": 0,
                "skipped_reason": "FORMUMIND_EXTERNAL_SUBSTITUTES=false",
                "provider": "pubchem_fastsimilarity_2d",
            },
        }

    identity = resolve_slot_identity(material, spec)
    smiles = identity.get("smiles") or ""
    if not smiles:
        return {
            "identity": identity,
            "external": [],
            "external_meta": {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": "无法解析 SMILES（聚合物/商品名常见）；仅展示库内候选",
                "provider": "pubchem_fastsimilarity_2d",
            },
        }

    try:
        rows = pubchem_similar_by_smiles(
            smiles,
            threshold=threshold,
            limit=limit,
            exclude_cid=identity.get("cid"),
        )
    except Exception as exc:
        logger.debug("external alternatives failed ({})", exc)
        return {
            "identity": identity,
            "external": [],
            "external_meta": {
                "enabled": True,
                "queried": True,
                "count": 0,
                "skipped_reason": f"联网检索失败：{exc}",
                "provider": "pubchem_fastsimilarity_2d",
            },
        }

    if role_hint:
        for row in rows:
            row["role_hint"] = role_hint

    reason = None if rows else "PubChem 未返回相似结构（或超时）"
    return {
        "identity": identity,
        "external": rows,
        "external_meta": {
            "enabled": True,
            "queried": True,
            "count": len(rows),
            "skipped_reason": reason,
            "provider": "pubchem_fastsimilarity_2d",
        },
    }
