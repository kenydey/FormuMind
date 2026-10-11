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

        from .fingerprints import morgan_bitvect

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        return morgan_bitvect(mol, 2, _FP_BITS).ToBitString()
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
    return index_chunks_structures([(chunk_id, source_id, text)], origin=origin)


def index_chunks_structures(chunks: list[tuple[str, str, str]],
                            origin: str = "chem_extract") -> int:
    """批量索引（单事务）。chunks: [(chunk_id, source_id, text)]。"""
    if not rdkit_available() or not chunks:
        return 0
    try:
        from .chem_extract import extract_smiles as extract_smiles_candidates
        from ..db.database import default_session_factory
        from ..db.models import StructureFingerprint

        # 先全部提取（无 DB 操作）
        to_add = []
        seen = set()
        for chunk_id, source_id, text in chunks:
            if not (text or "").strip():
                continue
            for cand in extract_smiles_candidates(text):
                canon = canonical_smiles(cand.get("canonical") or cand.get("raw") or "")
                if not canon or (chunk_id, canon) in seen:
                    continue
                fp_hex = smiles_to_fingerprint_hex(canon)
                if not fp_hex:
                    continue
                seen.add((chunk_id, canon))
                to_add.append((chunk_id, source_id, canon, fp_hex))
        if not to_add:
            return 0
        # 单事务写入
        n = 0
        with default_session_factory()() as session:
            for chunk_id, source_id, canon, fp_hex in to_add:
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
        logger.debug("index_chunks_structures failed: %s", exc)
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
        from ..db.database import default_session_factory
        from ..db.models import StructureFingerprint

        results = []
        with default_session_factory()() as session:
            # S-3: 前缀预过滤 —— 高阈值时指纹前 16 字符大概率相同，
            # 用 LIKE 走索引，大幅减少 Python 计算量。
            # 阈值 <0.5 时前缀过滤可能漏检，回退全表。
            if threshold >= 0.5:
                prefix = q_hex[:16]
                rows = session.query(StructureFingerprint).filter(
                    StructureFingerprint.fingerprint_hex.like(prefix + "%")
                ).all()
                # 前缀无命中时回退全表（避免漏检）
                if not rows:
                    rows = session.query(StructureFingerprint).all()
            else:
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
