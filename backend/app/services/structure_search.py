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


def substructure_hits(
    smarts: str,
    *,
    top_k: int = 20,
    settings=None,
) -> list[dict]:
    """Filter catalog materials by SMARTS substructure match.

    Returns [{"name", "role", "smiles"}...] for materials whose structure
    contains the given substructure (e.g. ``[NX3;H2]`` primary amine,
    ``c1ccccc1`` benzene ring). Empty list when RDKit unavailable, the SMARTS
    is invalid, or nothing matches. Best-effort: rows without SMILES or that
    fail to parse are skipped.
    """
    if not (smarts or "").strip():
        return []
    try:
        from rdkit import Chem

        patt = Chem.MolFromSmarts(smarts)
        if patt is None:
            return []
    except Exception as exc:
        logger.warning("structure_search: SMARTS parse failed: %s", exc)
        return []

    hits: list[dict] = []
    for name, role, cand_smiles in _material_candidates(settings=settings):
        try:
            cand = Chem.MolFromSmiles(cand_smiles)
            if cand is None:
                continue
            if cand.HasSubstructMatch(patt):
                hits.append({"name": name, "role": role, "smiles": cand_smiles})
        except Exception:
            continue
    return hits[:top_k]


def _kg_entities_with_smiles(settings=None, limit: int = 2000) -> list[tuple[str, str, str | None]]:
    """[(id, canonical_name, smiles)] from the KG entity store (chemical kind).

    P4: KG structure-similarity dimension — entities with a SMILES are
    candidates for Tanimoto ranking alongside catalog materials.
    """
    settings = settings or get_settings()
    out: list[tuple[str, str, str | None]] = []
    try:
        from ..db.entity_store import get_entity_store

        store = get_entity_store()
        rows = store.search_entities("", limit=limit)
        # search_entities("") returns [] — fall back to a direct scan.
        if not rows:
            from ..db.database import default_session_factory
            from ..db.models import KGEntity

            with default_session_factory()() as session:
                rows = (
                    session.query(KGEntity)
                    .filter(KGEntity.kind == "chemical", KGEntity.smiles.isnot(None))
                    .limit(limit)
                    .all()
                )
        for row in rows:
            s = getattr(row, "smiles", None)
            if s:
                name = getattr(row, "canonical_name", "") or getattr(row, "id", "")
                out.append((getattr(row, "id", ""), name, s))
    except Exception as exc:
        logger.debug("structure_search: kg entity scan skipped: %s", exc)
    return out


def _adaptive_kg_threshold(smiles: str, requested: float) -> float:
    """P4 调优: 大分子 Tanimoto 天然偏低（Morgan 指纹位多，交集占比小），
    固定阈值会让 KG 相似命中对聚合物/树脂类高分子恒为空。两档制：
    ≤15 原子小分子用请求阈值（0.6 保精度）；>15 大分子放宽到 0.25
    （文献实体多为小分子，与树脂类查询的交集天然稀疏，0.25 是实测
    DGEBA→环氧硅烷 0.28 的可达档）。"""
    try:
        from rdkit import Chem

        mol = Chem.MolFromSmiles(smiles)
        n_atoms = mol.GetNumAtoms() if mol else 0
    except Exception:
        return requested
    if n_atoms <= 15:
        return requested
    return max(0.15, min(requested, 0.25))


def kg_structure_hits(
    smiles: str,
    *,
    top_k: int = 10,
    threshold: float = 0.6,
    settings=None,
) -> list[dict]:
    """P4: rank KG chemical entities by Morgan Tanimoto to ``smiles``.

    Returns [{"id", "name", "smiles", "similarity"}...] desc. Empty when RDKit
    unavailable or nothing clears threshold. Complements catalog hits with the
    knowledge-graph dimension (entities from ingested literature).

    ``threshold`` is the caller's requested cutoff for small molecules; large
    ones (polymers/resins) get an adaptive relaxation via ``_adaptive_kg_threshold``.
    """
    if not (smiles or "").strip():
        return []
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem, DataStructs

        query = Chem.MolFromSmiles(smiles)
        if query is None:
            return []
        qfp = AllChem.GetMorganFingerprintAsBitVect(query, 2, nBits=2048)
        effective_threshold = _adaptive_kg_threshold(smiles, threshold)
    except Exception as exc:
        logger.warning("structure_search: kg query fingerprint failed: %s", exc)
        return []

    hits: list[dict] = []
    for eid, name, cand_smiles in _kg_entities_with_smiles(settings=settings):
        try:
            cand = Chem.MolFromSmiles(cand_smiles)
            if cand is None:
                continue
            cfp = AllChem.GetMorganFingerprintAsBitVect(cand, 2, nBits=2048)
            sim = float(DataStructs.TanimotoSimilarity(qfp, cfp))
        except Exception:
            continue
        if sim >= effective_threshold:
            hits.append(
                {
                    "id": eid,
                    "name": name,
                    "smiles": cand_smiles,
                    "similarity": round(sim, 4),
                }
            )
    hits.sort(key=lambda h: h["similarity"], reverse=True)
    return hits[:top_k]


def scaffold_substitutes(
    smiles: str,
    *,
    top_k: int = 10,
    settings=None,
) -> list[dict]:
    """P-D: Murcko 骨架替代发现 — 相同骨架 = 潜在结构替代。

    RDKit MurckoScaffold 提取查询分子骨架，返回材料库中**骨架一致**的
    材料（不同商品名/端基但核心环系相同 → drop-in 候选）。返回
    [{"name", "role", "smiles", "scaffold"}...]。RDKit 缺失或无效输入 → []。
    """
    if not (smiles or "").strip():
        return []
    try:
        from rdkit import Chem
        from rdkit.Chem.Scaffolds import MurckoScaffold

        query = Chem.MolFromSmiles(smiles)
        if query is None:
            return []
        q_scaffold = Chem.MolToSmiles(MurckoScaffold.GetScaffoldForMol(query))
    except Exception as exc:
        logger.warning("structure_search: scaffold extraction failed: %s", exc)
        return []

    hits: list[dict] = []
    for name, role, cand_smiles in _material_candidates(settings=settings):
        try:
            cand = Chem.MolFromSmiles(cand_smiles)
            if cand is None:
                continue
            c_scaffold = Chem.MolToSmiles(MurckoScaffold.GetScaffoldForMol(cand))
            if c_scaffold == q_scaffold:
                hits.append(
                    {"name": name, "role": role, "smiles": cand_smiles, "scaffold": c_scaffold}
                )
        except Exception:
            continue
    return hits[:top_k]
