"""The no-LLM fallback answer is a verbatim quote — the numeric gate must not call it unsourced (round-4).

``answer_question`` falls back to "根据已加载资料：<best snippet>" when no model answers (no API key — a supported
mode — or an upstream outage). The quote carried no ``[^1]`` marker, so ``check_answer_numbers`` found numbers
with no citation to bind to and appended "【数值核验】…在所引证据原文中未找到对应来源" to a sentence copied
character for character from that very source.
"""
from __future__ import annotations

from app.domain.schemas import Evidence
from app.services import llm
from app.services.numeric_check import check_answer_numbers

SNIPPET = "磷酸锌含量为 8 wt% 时，中性盐雾 (ASTM B117) 达到 1000 h，附着力 6.5 MPa。"


def _evidence(snippet: str = SNIPPET) -> Evidence:
    return Evidence(source="kb", identifier="kb:1#c0", title="磷酸锌盐雾研究", snippet=snippet, relevance=0.9)


def test_the_offline_quote_cites_its_source_and_passes_the_numeric_gate(monkeypatch):
    monkeypatch.setattr(llm, "_call_llm", lambda *a, **k: None)
    answer, relevant = llm.answer_question("磷酸锌含量对盐雾的影响", [_evidence()])
    assert answer.endswith("[^1]")
    assert SNIPPET in answer
    assert check_answer_numbers(answer, [e.snippet for e in relevant]) == []


def test_the_ellipsis_only_appears_when_the_quote_was_actually_cut(monkeypatch):
    monkeypatch.setattr(llm, "_call_llm", lambda *a, **k: None)
    short, _ = llm.answer_question("磷酸锌含量对盐雾的影响", [_evidence()])
    assert "…" not in short
    long_snippet = "磷酸锌盐雾" + "实验数据" * 100
    long_answer, _ = llm.answer_question("磷酸锌盐雾", [_evidence(long_snippet)])
    assert "…[^1]" in long_answer


def test_without_any_evidence_there_is_nothing_to_cite(monkeypatch):
    monkeypatch.setattr(llm, "_call_llm", lambda *a, **k: None)
    answer, relevant = llm.answer_question("完全无关的问题 quantum", [])
    assert "[^" not in answer and not relevant
