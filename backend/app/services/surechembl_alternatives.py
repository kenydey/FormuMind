"""SureChEMBL structure-similar alternatives for material substitution (P1).

Advisory only — no formula Δ. Failures degrade to empty list + meta.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from loguru import logger

from ..db.material_store import norm_key
from ..domain.knowledge import RAW_MATERIALS
from . import surechembl_client as client

_MULTI_FRAGMENT_RE = re.compile(r"\.")


def _match_catalog(smiles: str, name: str) -> tuple[bool, str | None]:
    smiles_n = (smiles or "").strip()
    name_n = norm_key(name or "")
    for cat_name, spec in RAW_MATERIALS.items():
        if smiles_n and str(spec.get("smiles") or "").strip() == smiles_n:
            return True, cat_name
        if name_n and norm_key(cat_name) == name_n:
            return True, cat_name
    return False, None


def _google_patent_url(doc_id: str) -> str | None:
    """Convert SureChEMBL SCPN (e.g. CN-104789083-B) to a Google Patents URL."""
    raw = (doc_id or "").strip()
    if not raw:
        return None
    compact = raw.replace("-", "").replace(" ", "")
    if len(compact) < 5:
        return None
    return f"https://patents.google.com/patent/{quote(compact)}"


def _noise_score(row: dict[str, Any]) -> float:
    """Higher is worse. Prefer single-fragment, named, frequent chemistries."""
    score = 0.0
    name = str(row.get("name") or "")
    smiles = str(row.get("smiles") or "")
    if _MULTI_FRAGMENT_RE.search(smiles):
        # Multi-component mixtures are common noise in SureChEMBL dumps.
        score += 2.0 + 0.3 * smiles.count(".")
    if name.count(" ") >= 6:
        score += 0.5
    low = name.casefold()
    for bad in ("phosphane", "nonakis", "bis(", "tris(", "complex", "mixture"):
        if bad in low:
            score += 1.0
    freq = row.get("global_frequency")
    try:
        freq_i = int(freq) if freq is not None else 0
    except (TypeError, ValueError):
        freq_i = 0
    if freq_i <= 1:
        score += 1.5
    elif freq_i < 5:
        score += 0.5
    return score


def _normalize_structure_hit(raw: dict[str, Any], *, query_smiles: str) -> dict[str, Any] | None:
    name = str(raw.get("name") or "").strip()
    smiles = str(raw.get("smiles") or "").strip() or None
    chemical_id = str(raw.get("chemical_id") or raw.get("id") or "").strip()
    if not chemical_id and not name and not smiles:
        return None
    if smiles and smiles == query_smiles.strip():
        return None
    try:
        sim = float(raw.get("similarity") or 0.0)
    except (TypeError, ValueError):
        sim = 0.0
    # API may return 0–1 or 0–100.
    if sim > 1.0:
        sim = sim / 100.0
    try:
        freq = int(raw.get("global_frequency")) if raw.get("global_frequency") is not None else None
    except (TypeError, ValueError):
        freq = None
    try:
        mw = float(raw.get("mol_weight")) if raw.get("mol_weight") is not None else None
    except (TypeError, ValueError):
        mw = None
    in_cat, cat_name = _match_catalog(smiles or "", name)
    return {
        "name": name or smiles or chemical_id,
        "chemical_id": chemical_id or None,
        "smiles": smiles,
        "inchi_key": (str(raw.get("inchi_key")).strip() if raw.get("inchi_key") else None),
        "formula": (str(raw.get("mol_formula")).strip() if raw.get("mol_formula") else None),
        "molar_mass": mw,
        "similarity": round(sim, 4),
        "global_frequency": freq,
        "source": "surechembl",
        "in_catalog": in_cat,
        "catalog_name": cat_name,
        "patents": [],
        "note": "专利化学相似物；未做配方 Δ",
        "_noise": _noise_score(raw),
    }


def _attach_patents(rows: list[dict[str, Any]], *, per_chem: int = 2) -> None:
    ids = [r["chemical_id"] for r in rows if r.get("chemical_id")]
    if not ids:
        return
    # Batch once; API returns mixed docs — attach same top docs lightly,
    # then try per-id for the first few high-similarity hits.
    try:
        shared = client.documents_for_chemicals(ids[:5], page=1, size=max(3, per_chem))
    except Exception as exc:
        logger.debug("surechembl docs batch failed ({})", exc)
        shared = []

    def _pat_rows(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for d in docs[:per_chem]:
            doc_id = str(d.get("docId") or d.get("doc_id") or "").strip()
            meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            titles = meta.get("titles") if isinstance(meta, dict) else None
            title = ""
            if isinstance(titles, list) and titles:
                first = titles[0] if isinstance(titles[0], dict) else {}
                arr = first.get("titles") if isinstance(first, dict) else None
                if isinstance(arr, list) and arr:
                    title = str(arr[0])
            out.append(
                {
                    "doc_id": doc_id,
                    "title": title,
                    "assignee": str(d.get("pa") or "") or None,
                    "pub_date": str((meta or {}).get("pd") or "") or None,
                    "url": _google_patent_url(doc_id),
                }
            )
        return out

    shared_pats = _pat_rows(shared)
    for i, row in enumerate(rows):
        if i < 3 and row.get("chemical_id"):
            try:
                docs = client.documents_for_chemicals([row["chemical_id"]], page=1, size=per_chem)
                pats = _pat_rows(docs) or shared_pats
            except Exception:
                pats = shared_pats
        else:
            pats = shared_pats
        row["patents"] = pats


def fetch_surechembl_alternatives(
    *,
    material: str,
    smiles: str | None,
    limit: int = 8,
    threshold: int = 85,
    role_hint: str | None = None,
    attach_patents: bool = True,
) -> dict[str, Any]:
    """Structure-similar SureChEMBL hits for a resolved SMILES."""
    if not client.surechembl_enabled():
        return {
            "surechembl": [],
            "surechembl_meta": {
                "enabled": False,
                "queried": False,
                "count": 0,
                "skipped_reason": "FORMUMIND_SURECHEMBL=false",
                "provider": "surechembl_api",
                "search_hash": None,
            },
        }

    display = (material or "").strip()
    smi = (smiles or "").strip()
    if not smi:
        return {
            "surechembl": [],
            "surechembl_meta": {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": "无法解析 SMILES；跳过 SureChEMBL 结构相似",
                "provider": "surechembl_api",
                "search_hash": None,
            },
        }

    lim = max(1, min(25, int(limit)))
    try:
        result = client.structure_search(
            smi, search_type="similarity", threshold=threshold, limit=max(lim * 2, lim)
        )
    except Exception as exc:
        logger.debug("surechembl structure search failed ({})", exc)
        return {
            "surechembl": [],
            "surechembl_meta": {
                "enabled": True,
                "queried": True,
                "count": 0,
                "skipped_reason": f"surechembl_error:{exc}",
                "provider": "surechembl_api",
                "search_hash": None,
            },
        }

    hits_raw = list(result.get("hits") or [])
    search_hash = result.get("search_hash")
    rows: list[dict[str, Any]] = []
    seen: set[str] = {norm_key(display)}
    thr_f = max(0.5, min(1.0, float(threshold) / 100.0))

    for raw in hits_raw:
        hit = _normalize_structure_hit(raw, query_smiles=smi)
        if not hit:
            continue
        if hit["similarity"] < thr_f:
            continue
        if hit["_noise"] >= 3.5:
            continue
        key = norm_key(hit["name"]) or (hit.get("smiles") or "")
        if key in seen:
            continue
        seen.add(key)
        if role_hint:
            hit["role_hint"] = role_hint
        rows.append(hit)

    rows.sort(key=lambda r: (-r["similarity"], r.get("_noise", 0.0), -(r.get("global_frequency") or 0)))
    rows = rows[:lim]
    for r in rows:
        r.pop("_noise", None)

    if attach_patents and rows:
        try:
            _attach_patents(rows, per_chem=2)
        except Exception as exc:
            logger.debug("surechembl attach patents failed ({})", exc)

    skipped = None
    if not rows:
        skipped = result.get("skipped_reason") or "SureChEMBL 未返回可用相似结构（或被噪声过滤）"

    return {
        "surechembl": rows,
        "surechembl_meta": {
            "enabled": True,
            "queried": True,
            "count": len(rows),
            "skipped_reason": skipped,
            "provider": "surechembl_api",
            "search_hash": search_hash,
        },
    }
