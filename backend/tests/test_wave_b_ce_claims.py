"""Wave B: per-section claims + chat CE flag path + regenerate meta."""
from __future__ import annotations

from app.pipeline.claim_checker import extract_claims_by_section
from app.services.llm import answer_question
from app.domain.schemas import Evidence


def test_extract_claims_by_section_samples_each_heading():
    md = (
        "## A\n\n"
        "本章说明环氧树脂交联后可提升盐雾耐受至七百小时以上。\n\n"
        "## B\n\n"
        "本章说明硅烷偶联剂改善附着力但需控制水解条件与 pH 窗口。\n\n"
        "## C\n\n"
        "本章说明 VOC 与排放合规要求需要在配方阶段前置评估。\n"
    )
    claims = extract_claims_by_section(md, per_section=2, max_total=10)
    assert len(claims) >= 2
    joined = " ".join(claims)
    assert "盐雾" in joined or "环氧" in joined
    assert "硅烷" in joined or "VOC" in joined or "合规" in joined


def test_answer_question_skips_ce_when_flag_off(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("FORMUMIND_CHAT_CROSS_ENCODER_ENABLED", "false")
    get_settings.cache_clear()
    called = {"ce": False}

    def boom(*a, **k):
        called["ce"] = True
        raise AssertionError("CE should not run")

    monkeypatch.setattr("app.services.rag.rerank_scored", boom)
    monkeypatch.setattr(
        "app.services.llm._call_with_deadline",
        lambda fn, s: "ok answer from stub",
    )
    monkeypatch.setattr("app.services.llm._paperqa_available", lambda: False)
    try:
        ans, cites = answer_question(
            "盐雾？",
            [
                Evidence(
                    source="kb",
                    identifier="kb:1#c0",
                    title="t",
                    snippet="epoxy",
                    relevance=0.9,
                )
            ],
            None,
        )
    finally:
        get_settings.cache_clear()
    assert ans
    assert called["ce"] is False
