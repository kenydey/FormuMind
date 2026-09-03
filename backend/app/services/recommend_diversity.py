"""Diversity selection for multi-formulation recommend (MMR)."""
from __future__ import annotations

from ..domain.schemas import Formulation


def _ingredient_jaccard(a: Formulation, b: Formulation) -> float:
    sa = {i.name.lower() for i in a.ingredients if i.name}
    sb = {i.name.lower() for i in b.ingredients if i.name}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def formulation_similarity(a: Formulation, b: Formulation) -> float:
    try:
        from . import chemtools

        if chemtools.gateway_enabled():
            return float(chemtools.formulation_similarity(a, b))
    except Exception:
        pass
    return _ingredient_jaccard(a, b)


def select_diverse_mmr(
    forms: list[Formulation],
    n: int,
    *,
    lambda_score: float = 0.7,
) -> tuple[list[Formulation], bool]:
    """Maximal Marginal Relevance selection on score-sorted candidates."""
    if n <= 0 or not forms:
        return [], False
    if len(forms) <= n:
        return list(forms), False

    remaining = list(forms)
    selected: list[Formulation] = [remaining.pop(0)]
    max_score = max((f.score or 0.0) for f in forms) or 1.0

    while len(selected) < n and remaining:
        best_idx = 0
        best_mmr = float("-inf")
        for idx, cand in enumerate(remaining):
            norm_score = (cand.score or 0.0) / max_score
            max_sim = max(formulation_similarity(cand, s) for s in selected)
            mmr = lambda_score * norm_score + (1.0 - lambda_score) * (1.0 - max_sim)
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = idx
        selected.append(remaining.pop(best_idx))

    return selected, True
