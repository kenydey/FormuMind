"""SureChEMBL identification helpers — normalize API hits for chemical_lookup."""
from __future__ import annotations

import re
from typing import Any

from loguru import logger

from . import surechembl_client as client

_SMILES_CHARS_RE = re.compile(r"^[A-Za-z0-9@+\-\[\]\(\)=#$/\\%.:*]+$")


def _looks_like_smiles(text: str) -> bool:
    t = (text or "").strip()
    return 1 < len(t) <= 500 and " " not in t and bool(_SMILES_CHARS_RE.match(t))


def _norm_hit(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    chemical_id = str(raw.get("chemical_id") or raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    smiles = raw.get("smiles")
    smiles_s = str(smiles).strip() if smiles else None
    if not chemical_id and not name and not smiles_s:
        return None
    freq = raw.get("global_frequency")
    try:
        freq_i = int(freq) if freq is not None and str(freq) != "" else None
    except (TypeError, ValueError):
        freq_i = None
    mw = raw.get("mol_weight")
    try:
        mw_f = float(mw) if mw is not None else None
    except (TypeError, ValueError):
        mw_f = None
    # Soft-drop empty frequency + no structure.
    if freq_i == 0 and not smiles_s:
        return None
    return {
        "chemical_id": chemical_id,
        "name": name or smiles_s or chemical_id,
        "smiles": smiles_s,
        "inchi": (str(raw.get("inchi")).strip() if raw.get("inchi") else None),
        "inchi_key": (str(raw.get("inchi_key")).strip() if raw.get("inchi_key") else None),
        "mol_weight": mw_f,
        "global_frequency": freq_i,
        "source": "surechembl",
        "source_url": (
            f"https://www.surechembl.org/chemical/{chemical_id}" if chemical_id else None
        ),
    }


def lookup_surechembl(query: str) -> dict[str, Any] | None:
    """Resolve a name or SMILES via SureChEMBL; return chemical_lookup-shaped dict."""
    q = (query or "").strip()
    if not q or not client.surechembl_enabled():
        return None

    hits: list[dict[str, Any]] = []
    try:
        if _looks_like_smiles(q):
            by_smi = client.get_by_smiles(q)
            if by_smi:
                hits.append(by_smi)
        for row in client.get_by_name(q):
            hits.append(row)
    except Exception as exc:
        logger.debug("surechembl lookup failed ({})", exc)
        return None

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in hits:
        hit = _norm_hit(raw)
        if not hit:
            continue
        key = (
            (hit.get("inchi_key") or "").casefold()
            or (hit.get("smiles") or "").casefold()
            or (hit.get("name") or "").casefold()
            or hit.get("chemical_id")
            or ""
        )
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(hit)

    if not normalized:
        return None

    # Prefer higher corpus frequency, then having SMILES.
    normalized.sort(
        key=lambda h: (
            -(h.get("global_frequency") or 0),
            0 if h.get("smiles") else 1,
            h.get("name") or "",
        )
    )
    best = normalized[0]
    return {
        "query": query,
        "cas": "",  # SureChEMBL does not reliably provide CAS — never invent.
        "iupac_name": best.get("name") or q,
        "zh_name": "",
        "formula": "",
        "smiles": best.get("smiles"),
        "molar_mass": best.get("mol_weight"),
        "found": True,
        "source": "surechembl",
        "providers_tried": ["surechembl"],
        "surechembl": {
            "chemical_id": best.get("chemical_id"),
            "global_frequency": best.get("global_frequency"),
            "inchi_key": best.get("inchi_key"),
            "source_url": best.get("source_url"),
            "alternates": [
                {
                    "chemical_id": h.get("chemical_id"),
                    "name": h.get("name"),
                    "smiles": h.get("smiles"),
                    "global_frequency": h.get("global_frequency"),
                }
                for h in normalized[1:5]
            ],
        },
    }
