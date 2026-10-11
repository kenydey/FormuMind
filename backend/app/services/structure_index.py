"""P1-3: RDKit 化学结构索引 —— Morgan 指纹 + Tanimoto 相似搜索。

设计：
- 指纹：Morgan 2048bit（radius=2），存 hex。
- 索引：在 chunk 入库时提取 SMILES → 算指纹 → 存 structure_fingerprints。
- 检索：查询 SMILES → 指纹 → 与库内指纹算 Tanimoto → 按相似度排序。
- fail-open：RDKit 不可用时全部中性返回。

不是第二套检索引擎：与 document_chunks 按 chunk_id 关联的扩展索引，
检索结果回落到 chunk 文本，与现有混合检索并列。
"""
from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

_FP_BITS = 2048


def rdkit_available() -> bool:
    try:
        __import__("rdkit")
        return True
    except Exception:
        return False


def smiles_to_fingerprint_hex(smiles: str) -> str | None:
    """SMILES → Morgan 指纹 hex。失败返回 None。"""
    if not rdkit_available() or not smiles:
        return None
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        fp = AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=_FP_BITS)
        return fp.ToBitString()
    except Exception:
        return None


def canonical_smiles(smiles: str) -> str | None:
    """规范 SMILES。失败返回 None。"""
    if not rdkit_available() or not smiles:
        return None
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles)
        return Chem.MolToSmiles(mol) if mol else None
    except Exception:
        return None


def tanimoto_hex(hex1: str, hex2: str) -> float:
    """两 hex 指纹的 Tanimoto 相似度（bit string 形式）。"""
    if not hex1 or not hex2 or len(hex1) != len(hex2):
        return 0.0
    try:
        a = int(hex1, 2)
        b = int(hex2, 2)
        inter = bin(a & b).count("1")
        union = bin(a | b).count("1")
        return inter / union if union else 0.0
    except Exception:
        return 0.0


def index_chunk_structures(chunk_id: str, source_id: str, text: str,
                           origin: str = "chem_extract") -> int:
    """从 chunk 文本提取 SMILES 并索引。返回索引条数。fail-open。"""
    if not rdkit_available() or not (text or "").strip():
        return 0
    try:
        from .chem_extract import extract_smiles as extract_smiles_candidates
        from ..db.database import get_session
        from ..db.models import StructureFingerprint

        candidates = extract_smiles_candidates(text)
        if not candidates:
            return 0
        n = 0
        with get_session() as session:
            for cand in candidates:
                canon = canonical_smiles(cand.get("canonical") or cand.get("raw") or "")
                if not canon:
                    continue
                fp_hex = smiles_to_fingerprint_hex(canon)
                if not fp_hex:
                    continue
                # 去重：同一 chunk 同一 SMILES 只存一次
                exists = session.query(StructureFingerprint).filter_by(
                    chunk_id=chunk_id, smiles=canon).first()
                if exists:
                    continue
                session.add(StructureFingerprint(
                    id=uuid.uuid4().hex,
                    chunk_id=chunk_id,
                    source_id=source_id,
                    smiles=canon,
                    fingerprint_hex=fp_hex,
                    origin=origin,
                ))
                n += 1
            session.commit()
        return n
    except Exception as exc:
        logger.debug("index_chunk_structures failed: %s", exc)
        return 0


def search_similar_structures(query_smiles: str, *,
                              threshold: float = 0.7,
                              limit: int = 20) -> list[dict]:
    """Tanimoto 相似结构搜索。返回 [{chunk_id, source_id, smiles, similarity}]。"""
    if not rdkit_available():
        return []
    q_hex = smiles_to_fingerprint_hex(query_smiles)
    if not q_hex:
        return []
    try:
        from ..db.database import get_session
        from ..db.models import StructureFingerprint

        results = []
        with get_session() as session:
            rows = session.query(StructureFingerprint).all()
            for r in rows:
                sim = tanimoto_hex(q_hex, r.fingerprint_hex)
                if sim >= threshold:
                    results.append({
                        "chunk_id": r.chunk_id,
                        "source_id": r.source_id,
                        "smiles": r.smiles,
                        "similarity": round(sim, 3),
                        "origin": r.origin,
                    })
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]
    except Exception as exc:
        logger.debug("search_similar_structures failed: %s", exc)
        return []
