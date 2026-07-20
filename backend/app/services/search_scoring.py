"""Chemical-entity relevance boosts for live search Evidence rows."""
from __future__ import annotations

from ..domain.schemas import Evidence


def query_chem_context(query: str) -> dict:
    """Extract CAS / formula / SMILES signals from a search query."""
    ctx: dict = {"cas": set(), "formulas": set(), "smiles": []}
    if not (query or "").strip():
        return ctx
    try:
        from .chem_extract import extract_cas, extract_formulas, extract_smiles

        ctx["cas"] = set(extract_cas(query))
        ctx["formulas"] = set(extract_formulas(query))
        ctx["smiles"] = [s["canonical"] for s in extract_smiles(query)]
    except Exception:
        pass
    return ctx


def evidence_entity_boost(ev: Evidence, qctx: dict) -> float:
    """Additive score bump when evidence text shares query chemical entities."""
    if not any(qctx.get(k) for k in ("cas", "formulas", "smiles")):
        return 0.0
    blob = f"{ev.title} {ev.snippet} {ev.identifier}".lower()
    boost = 0.0
    for cas in qctx.get("cas") or []:
        if str(cas).lower() in blob:
            boost += 0.3
            break
    for formula in qctx.get("formulas") or []:
        if str(formula).lower() in blob:
            boost += 0.15
            break
    return min(boost, 0.45)
