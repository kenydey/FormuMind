"""B-2: 上下文 token 预算 + 摘要压缩。

替代 12 轮硬截断：token 超限时保留最新轮次、旧轮次折叠为确定性摘要，
早期关键实体（CAS/化学术语/首个问题）不丢失；prompt 总长度有界。
"""
from app.domain.chat_schemas import ChatTurn
from app.services.chat_context import trim_history


def _turns(n, prefix="第{i}轮讨论配方工艺内容填充文本"):
    return [
        ChatTurn(role="user" if i % 2 == 0 else "assistant", content=prefix.format(i=i) * 6)
        for i in range(n)
    ]


def test_short_history_unchanged_no_summary():
    hist = _turns(4)
    out = trim_history(hist, max_turns=12, token_budget=6000)
    assert len(out) == 4
    assert all(not t.content.startswith("[历史摘要]") for t in out)


def test_over_budget_keeps_newest_and_summarizes_dropped():
    hist = _turns(20)
    # 首轮埋关键实体，验证摘要保留
    hist[0] = ChatTurn(role="user", content="磷酸锌的CAS号是多少？7779-90-0 在配方中的作用")
    out = trim_history(hist, max_turns=100, token_budget=400)
    assert out[0].content.startswith("[历史摘要]")
    assert "7779-90-0" in out[0].content or "磷酸锌" in out[0].content
    assert "此前" in out[0].content and "轮对话" in out[0].content
    # 最新轮次保留
    assert out[-1].content == hist[-1].content
    # 摘要 + 保留轮次，总长度有界
    from app.services.query_aware_compression import estimate_tokens

    assert sum(estimate_tokens(t.content) for t in out) <= 400 + 400


def test_max_turns_backstop_still_applies():
    hist = _turns(20)
    out = trim_history(hist, max_turns=5, token_budget=10**9)
    assert len(out) == 5
    assert out[-1].content == hist[-1].content


def test_single_huge_turn_never_dropped():
    hist = [ChatTurn(role="user", content="x" * 7000)]
    out = trim_history(hist, max_turns=12, token_budget=10)
    assert len(out) == 1
    assert out[0].content == hist[0].content


def test_zero_budget_falls_back_to_hard_cut():
    hist = _turns(20)
    out = trim_history(hist, max_turns=5, token_budget=0)
    assert len(out) == 5
    assert not any(t.content.startswith("[历史摘要]") for t in out)
