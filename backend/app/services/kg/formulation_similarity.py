"""Formulation similarity algorithms for cross-project knowledge reuse."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

def _chemical_name_similarity(a: str, b: str) -> float:
    """未匹配成分的化学相似度, 三级: 归一化精确 → RDKit 指纹 → 词法兜底。

    2026-09-04 (P2+R5): 原实现 ``q_parts & c_parts`` 词法拆分让 "Waterborne
    epoxy resin" 与 "Waterborne polyurethane resin" 共享 2/3 词得 0.5×weight
    加分(化学荒谬)。P2 替换为归一化精确(1.0)+ 词法 0.15 兜底; R5 成分
    结构回填后插入指纹级: 双方解析到 RAW_MATERIALS 且都有 SMILES 时用
    Morgan fingerprint (radius=2, 2048bit) Tanimoto, ≥0.6 才计(低于不算
    命中); 无结构 → 词法 0.15 兜底。任何异常静默降级(永不打断推荐)。
    """
    try:
        from ...domain.knowledge import RAW_MATERIALS, resolve_material_name

        if a.strip().lower() == b.strip().lower():
            return 1.0  # 同名防御(调用方已做集合差, 直调安全网)
        na = resolve_material_name(a)
        nb = resolve_material_name(b)
        if na and nb and na.lower() == nb.lower():
            return 1.0  # ① 别名/目录归一化后同物(真同一材料)
        sa = RAW_MATERIALS.get(na, {}).get("smiles") if na else None
        sb = RAW_MATERIALS.get(nb, {}).get("smiles") if nb else None
        if sa and sb:  # ② 双方都有结构 → Morgan/Tanimoto
            sim = _tanimoto_similarity(sa, sb)
            if sim is not None:
                return sim if sim >= _TANIMOTO_MIN else 0.0
    except Exception:
        pass
    q_parts = set(a.lower().split())
    c_parts = set(b.lower().split())
    if not q_parts or not c_parts:
        return 0.0
    overlap = q_parts & c_parts
    if len(overlap) >= max(2, min(len(q_parts), len(c_parts)) * 0.5):
        return 0.15  # ③ 词法低置信兜底(无结构数据的最后手段)
    return 0.0


# Tanimoto 有意义阈值: <0.6 视为化学不相似(不给加分)。
_TANIMOTO_MIN = 0.6


def _tanimoto_similarity(smi_a: str, smi_b: str) -> float | None:
    """Morgan 指纹 Tanimoto; RDKit 缺失/解析失败 → None(调用方降级)。"""
    try:
        from rdkit import Chem  # type: ignore
        from rdkit.Chem import AllChem, DataStructs  # type: ignore

        mol_a = Chem.MolFromSmiles(smi_a)
        mol_b = Chem.MolFromSmiles(smi_b)
        if mol_a is None or mol_b is None:
            return None
        fp_a = AllChem.GetMorganFingerprintAsBitVect(mol_a, 2, nBits=2048)
        fp_b = AllChem.GetMorganFingerprintAsBitVect(mol_b, 2, nBits=2048)
        return float(DataStructs.TanimotoSimilarity(fp_a, fp_b))
    except Exception:
        return None


_ROLE_WEIGHTS = {
    "resin": 1.0, "hardener": 1.0, "catalyst": 0.8,
    "pigment": 0.5, "filler": 0.5, "solvent": 0.4,
    "additive": 0.4, "inhibitor": 0.7, "unknown": 0.3,
}

def formulation_similarity(
    query_factors: dict[str, float],
    candidate_factors: dict[str, float],
    query_roles: dict[str, str] | None = None,
    candidate_roles: dict[str, str] | None = None,
    kg_bonus: bool = True,
) -> float:
    if not query_factors or not candidate_factors:
        return 0.0
    all_ingredients = set(query_factors.keys()) | set(candidate_factors.keys())
    if not all_ingredients:
        return 0.0
    q_roles = query_roles or {}
    c_roles = candidate_roles or {}
    role_groups: dict[str, list[tuple[str, float, float]]] = {}
    for ing in all_ingredients:
        q_val = query_factors.get(ing, 0.0)
        c_val = candidate_factors.get(ing, 0.0)
        role = q_roles.get(ing, c_roles.get(ing, "unknown"))
        role_groups.setdefault(role, []).append((ing, q_val, c_val))
    total_score = 0.0
    total_weight = 0.0
    for role, ingredients in role_groups.items():
        weight = _ROLE_WEIGHTS.get(role, 0.3)
        role_score = 0.0
        role_weight = 0.0
        q_ings = {ing for ing, qv, cv in ingredients if qv > 0}
        c_ings = {ing for ing, qv, cv in ingredients if cv > 0}
        for ing, qv, cv in ingredients:
            if qv > 0 and cv > 0:
                sum_val = qv + cv
                dosage_sim = 1.0 - abs(qv - cv) / sum_val if sum_val > 0 else 1.0
                role_score += dosage_sim * weight
                role_weight += weight
        q_only = q_ings - c_ings
        c_only = c_ings - q_ings
        if kg_bonus and q_only and c_only:
            for q_ing in q_only:
                for c_ing in c_only:
                    ing_sim = _chemical_name_similarity(q_ing, c_ing)
                    if ing_sim > 0:
                        role_score += ing_sim * weight
                        # 分母固定半权折扣: 若按 ing_sim 加权则归一化抵消
                        # (x·w)/(x·w)=1.0 —— 旧代码词法命中实际把该 role 相似度
                        # 拉满到 1.0, 比"0.5 加分"更糟。固定 0.5 → 未匹配对
                        # 对相似度的贡献封顶 ~0.3, 不再虚高。
                        role_weight += weight * 0.5
        if role_weight > 0:
            total_score += role_score
            total_weight += role_weight
    if total_weight == 0:
        return 0.0
    return min(1.0, total_score / total_weight)

def find_similar_formulations(
    query_factors: dict[str, float],
    all_experiments: list[dict[str, Any]],
    domain: str | None = None,
    exclude_project_id: str | None = None,
    min_similarity: float = 0.3,
    limit: int = 10,
) -> list[dict[str, Any]]:
    candidates = []
    for exp in all_experiments:
        if domain and exp.get("domain") != domain:
            continue
        if exclude_project_id and exp.get("project_id") == exclude_project_id:
            continue
        exp_factors = exp.get("factors", {})
        if not exp_factors:
            continue
        sim = formulation_similarity(query_factors, exp_factors)
        if sim >= min_similarity:
            candidates.append({
                "experiment_id": exp["id"],
                "project_id": exp.get("project_id", ""),
                "similarity": round(sim, 3),
                "factors": exp_factors,
                "measured": exp.get("measured", {}),
            })
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates[:limit]
