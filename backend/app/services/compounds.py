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


def _pubchempy_available() -> bool:
    try:
        import pubchempy  # noqa: F401

        return True
    except Exception:
        return False


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
        out: dict = {}
        smiles = getattr(c, "canonical_smiles", None) or getattr(c, "isomeric_smiles", None)
        if smiles:
            out["smiles"] = smiles
        if getattr(c, "molecular_weight", None):
            out["molar_mass"] = float(c.molecular_weight)
        if getattr(c, "xlogp", None) is not None:
            out["xlogp"] = float(c.xlogp)
        return out or None
    except Exception:
        return None


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
