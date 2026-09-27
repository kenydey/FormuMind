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


def _text_jaccard(a: set[str], b: set[str]) -> float:
    """Token 集合 Jaccard 相似度（任一为空 → 0）。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def select_diverse_mmr_text(
    items: list[Evidence],
    n: int,
    *,
    lambda_score: float = 0.7,
) -> list[Evidence]:
    """检索结果文本 MMR 多样性重排（Wave 1 · P0-10）。

    输入是 ``_merge_filter_rank`` 排好序的 Evidence 列表；相似度 = title+snippet
    token Jaccard（分词复用 ``literature._keywords`` 的中英双语分词器）。
    分数用「排名倒数归一化」（排位越前分数越高），因此 ``lambda_score=1.0``
    时退化为原序返回。``n >= len(items)`` 时做全量 MMR 重排（返回原集合的
    多样性排序）；``n`` 更小时只取前 n 个。返回新列表，不改变输入。
    """
    if n <= 0 or not items:
        return []

    from .literature import _keywords  # 延迟导入：避免循环依赖

    tokens = [_keywords(f"{e.title or ''} {e.snippet or ''}") for e in items]
    total = len(items)
    rank_scores = [1.0 - i / total for i in range(total)]
    k = min(n, total)

    remaining = list(range(total))
    selected = [remaining.pop(0)]
    while len(selected) < k and remaining:
        best_pos = 0
        best_mmr = float("-inf")
        for pos, idx in enumerate(remaining):
            max_sim = max(_text_jaccard(tokens[idx], tokens[s]) for s in selected)
            mmr = lambda_score * rank_scores[idx] + (1.0 - lambda_score) * (1.0 - max_sim)
            if mmr > best_mmr:
                best_mmr = mmr
                best_pos = pos
        selected.append(remaining.pop(best_pos))
    return [items[i] for i in selected]
