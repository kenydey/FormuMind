"""查询翻译(D3, 2026-09-04): 中文问 → 英文, 供英文子库检索。

双语分流的跨语通道: 中文问题在中文子库(bge)检索之外, 经 LLM 翻译成
英文后在英文子库(MiniLM)再检一轮, 解决"中文问 × 90% 英文语料"的
错配。严格降级: 翻译失败/超时 → None, 调用方只走中文子库(不劣于
现状); 只译到英文(当前语料 87% 英文), 不做 en→zh。

提示词要求输出纯英文技术查询(术语保真: 牌号/化学品名不译错)。
"""
from __future__ import annotations

_TRANSLATE_PROMPT = (
    "你是金属表面处理领域的检索查询翻译器。把下面的中文研究问题翻译成"
    "英文检索查询, 用于在英文专利/文献库中检索。要求: 只输出翻译后的英文,"
    "不要解释; 保留化学品名/牌号/缩写不译(如 Bonderite、DGEBA、salt spray);"
    "术语用领域惯用表达(耐盐雾=salt spray resistance, 钝化=passivation/"
    "conversion coating, 自沉积=autodeposition)。\n\n"
    "中文问题:\n{query}"
)


def translate_query_zh_to_en(query: str, *, timeout_s: float = 15.0) -> str | None:
    """中文 → 英文检索查询; 失败/超时/输出不可用 → None(调用方降级)。

    复用 llm 私有通道(_call_llm + _call_with_deadline 硬超时), 不新增
    依赖; 空输入/非中文输入直接返回 None(不浪费一次 LLM 调用)。
    """
    q = (query or "").strip()
    if not q:
        return None
    try:
        from . import llm as _llm

        raw = _llm._call_with_deadline(
            lambda: _llm._call_llm(_TRANSLATE_PROMPT.format(query=q)),
            timeout_s,
        )
    except Exception:
        return None
    if not raw:
        return None
    out = str(raw).strip().strip('"').strip("```").strip()
    # 输出必须成段英文(≥10 个 ASCII 字母), 防止模型回中文/空话。
    letters = sum(1 for ch in out if ch.isascii() and ch.isalpha())
    if letters < 10 or len(out) > 600:
        return None
    return out
