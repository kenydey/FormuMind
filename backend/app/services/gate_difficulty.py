"""B-5: 问答难度估计，供 reviewer 与拒答门限自适应使用。

确定性启发式（无 LLM、无 I/O）：
- 答案数值密度：数值多的答案 stakes 更高，更难逐一核验；
- 论断数量：论断越多，全部证实越难；
- 问题长度：长问题通常更复杂。

返回 0.0（简单）.. 1.0（困难）。
"""
from __future__ import annotations


def estimate_difficulty(
    *,
    question: str = "",
    answer: str = "",
    n_claims: int = 0,
) -> float:
    from .numeric_check import extract_numbers

    n_nums = len(extract_numbers(answer or ""))
    score = min(n_nums / 5.0, 1.0) * 0.5
    score += min(max(int(n_claims or 0) - 2, 0) / 6.0, 1.0) * 0.3
    qlen = len(question or "")
    score += min(max(qlen - 20, 0) / 180.0, 1.0) * 0.2
    return round(min(score, 1.0), 3)


def adaptive_abstention_threshold(base: float, difficulty: float) -> float:
    """难度自适应拒答门限：问题越难，门限越紧（最多收紧 20%），更容易拒答。

    简单问答（difficulty=0）时恒等于 base，保持 P2-2  pin 住的 0.5 边界行为。
    """
    d = min(max(float(difficulty or 0.0), 0.0), 1.0)
    return round(float(base) * (1.0 - 0.2 * d), 4)
