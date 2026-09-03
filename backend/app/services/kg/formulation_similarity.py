"""Formulation similarity algorithms for cross-project knowledge reuse."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

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
                    q_parts = set(q_ing.lower().split())
                    c_parts = set(c_ing.lower().split())
                    overlap = q_parts & c_parts
                    if overlap and len(overlap) >= min(len(q_parts), len(c_parts)) * 0.3:
                        role_score += 0.5 * weight
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
