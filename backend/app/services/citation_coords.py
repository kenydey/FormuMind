"""v25: reviewer 引用坐标对齐的共享函数。

答案中的 ``[^n]`` 基于完整 citations 编号，LLM reviewer 看到的却是过滤 wiki
编译页后的列表。调用方先用 :func:`claims_evidence_with_mapping` 拿到
``(过滤后列表, old_idx → new_idx 映射)``，再用 :func:`remap_citation_numbers`
重写答案中的 ``[^n]``；指向被过滤条目的标记会被删除。

chat.py 与 reviewer_fix_loop.py 共用，消除双实现漂移。
"""
from __future__ import annotations


def claims_evidence(evidence: list) -> list:
    """Strip Wiki rows so claims cannot cite compiled pages as Raw proof."""
    try:
        from .wiki.retrieve import filter_raw_evidence

        return filter_raw_evidence(evidence)
    except Exception:
        return evidence


def claims_evidence_with_mapping(evidence: list) -> tuple[list, dict[int, int]]:
    """返回 (过滤后列表, old_idx → new_idx 映射)，供 reviewer 坐标对齐用。

    用 id() 建立 old → new 映射（Evidence 可能不可哈希）。
    """
    filtered = claims_evidence(evidence)
    id_to_new = {id(e): i for i, e in enumerate(filtered)}
    mapping = {
        old: id_to_new[id(e)] for old, e in enumerate(evidence) if id(e) in id_to_new
    }
    return filtered, mapping


def remap_citation_numbers(answer: str, mapping: dict[int, int]) -> str:
    """用 old → new 映射重写答案中的 ``[^n]``，删除指向被过滤条目的标记。

    ``[^1]`` 是 1-based，mapping 的 key 是 0-based old_idx。
    """
    import re

    def _repl(m: re.Match) -> str:
        old_idx = int(m.group(1)) - 1
        new_idx = mapping.get(old_idx)
        if new_idx is None:
            return ""  # 指向 wiki（被过滤），删除标记
        return f"[^{new_idx + 1}]"

    return re.sub(r"\[\^(\d+)\]", _repl, answer)
