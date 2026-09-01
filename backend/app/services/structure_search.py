"""Chemical structure search — SMILES → similar materials in the catalog.

After MolScribe converts an uploaded structure image to SMILES, this module
finds structurally similar raw materials from the persisted catalog
(``MaterialRow.smiles`` / ``KBProduct.smiles``) using Morgan-fingerprint
Tanimoto similarity (reusing ``chemtools.mol_similarity``).

The hit names are then injected into text retrieval as query context, so a
structure image can drive both project creation and Q&A retrieval.
"""
from __future__ import annotations

import logging

from ..config import get_settings
from ..services.chemtools import mol_similarity

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.6
_DEFAULT_TOP_K = 5


def _material_candidates(settings=None) -> list[tuple[str, str, str | None]]:
    """[(name, role, smiles)] from the material store and KB products.

    Only rows with a parseable-looking SMILES are returned — the caller
    canonicalizes and validates before similarity work.
    """
    settings = settings or get_settings()
    out: list[tuple[str, str, str | None]] = []
    try:
        from ..db.material_store import get_material_store

        store = get_material_store()
        for row in store.list_all(limit=None):
            if row.smiles:
                out.append((row.name, row.role, row.smiles))
    except Exception as exc:
        logger.warning("structure_search: material store scan failed: %s", exc)

    # KBProduct rows (literature-promoted products) — best effort.
    try:
        from ..db.product_store import get_product_store

        pstore = get_product_store()
        for row in pstore.search(q="", limit=500):
            if getattr(row, "smiles", None):
                display = row.trade_name or row.generic_name or "kb-product"
                out.append((display, row.role or "kb", row.smiles))
    except Exception as exc:
        logger.debug("structure_search: kb store scan skipped: %s", exc)
    return out


def similarity_hits(
    smiles: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    threshold: float = _DEFAULT_THRESHOLD,
    settings=None,
) -> list[dict]:
    """Rank catalog materials by Morgan Tanimoto similarity to ``smiles``.

    Returns [{"name", "role", "smiles", "similarity"}...] sorted desc, capped
    at ``top_k``. Empty list when RDKit unavailable, no candidates, or nothing
    clears ``threshold``. Invalid query SMILES → [].
    """
    if not (smiles or "").strip():
        return []
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
        from rdkit.Chem import DataStructs

        query = Chem.MolFromSmiles(smiles)
        if query is None:
            return []
        qfp = AllChem.GetMorganFingerprintAsBitVect(query, 2, nBits=2048)
    except Exception as exc:
        logger.warning("structure_search: query fingerprint failed: %s", exc)
        return []

    hits: list[dict] = []
    for name, role, cand_smiles in _material_candidates(settings=settings):
        try:
            cand = Chem.MolFromSmiles(cand_smiles)
            if cand is None:
                continue
            cfp = AllChem.GetMorganFingerprintAsBitVect(cand, 2, nBits=2048)
            sim = float(DataStructs.TanimotoSimilarity(qfp, cfp))
        except Exception:
            continue
        if sim >= threshold:
            hits.append(
                {
                    "name": name,
                    "role": role,
                    "smiles": cand_smiles,
                    "similarity": round(sim, 4),
                }
            )
    hits.sort(key=lambda h: h["similarity"], reverse=True)
    return hits[:top_k]


def structure_query_terms(hits: list[dict]) -> str:
    """Collapse similarity hits into a query-context string for text retrieval.

    e.g. "Bisphenol A epoxy resin 双酚A环氧树脂 E51 …" — names (EN + zh) joined
    so BM25/vector retrieval can match documents that mention these materials.
    """
    terms: list[str] = []
    for h in hits:
        name = h.get("name")
        if name and name not in terms:
            terms.append(name)
    return " ".join(terms[:5])
