"""Tests for evidence_reviewer LLM rubric pass (W1-1)."""
from __future__ import annotations

import pytest

from app.services import evidence_reviewer as er


class _Settings:
    evidence_reviewer_enabled = True
    evidence_reviewer_llm_enabled = True


class _SettingsLLMOff:
    evidence_reviewer_enabled = True
    evidence_reviewer_llm_enabled = False


class _SettingsOff:
    evidence_reviewer_enabled = False
    evidence_reviewer_llm_enabled = True


def _patch_complete_json(monkeypatch: pytest.MonkeyPatch, fake):
    monkeypatch.setattr("app.services.llm.complete_json", fake)


def _patch_heuristic(monkeypatch: pytest.MonkeyPatch):
    """隔离 chat_claims 的重逻辑，让启发式回退分支确定性返回空 claims。"""
    monkeypatch.setattr(
        "app.services.chat_claims.build_sourced_claims",
        lambda question, answer, sources, structured=None, settings=None: [],
    )


def test_rubric_prompt_covers_required_sections():
    # 模板必须包含方案要求的各节
    for marker in ["§5.7", "§5.8", "§5.4", "§5.2", "§5.3", "§5.9", "§5.11"]:
        assert marker in er.REVIEWER_RUBRIC_PROMPT, marker
    assert "mere absence" in er.REVIEWER_RUBRIC_PROMPT


def test_57_unfounded_claim_not_convicted(monkeypatch: pytest.MonkeyPatch):
    """§5.7：查不到来源 → 不定罪、不 warn → pass。"""
    calls: dict = {}

    def fake(prompt: str):
        calls["prompt"] = prompt
        return {"status": "pass", "findings": [], "notes": ["无可查证断言"]}

    _patch_complete_json(monkeypatch, fake)
    res = er.review_answer(
        "X 工艺的常用温度是多少？",
        "据文献报道，X 工艺常用温度为 80℃[^1]。",
        [{"title": "t1", "doi": "10.1/a", "snippet": "工艺综述摘要"}],
        settings=_Settings(),
    )
    assert "prompt" in calls, "flag 开启时应调用 LLM"
    assert "§5.7" in calls["prompt"]
    assert res is not None
    assert res["status"] == "pass"
    assert res["unsupported_count"] == 0
    assert res["weak_count"] == 0
    assert res["suggestion"] is None


def test_58_fabricated_doi_convicted(monkeypatch: pytest.MonkeyPatch):
    """§5.8：编造具体标识符且全会话无 trace → failure。"""

    def fake(prompt: str):
        return {
            "status": "failure",
            "findings": [
                {
                    "rule": "5.8",
                    "severity": "blocking",
                    "title": "伪造引用：DOI 在会话中无 trace",
                    "detail": "回答引用 DOI 10.9999/xxxx，但 CITATIONS 与会话记录中均未出现",
                    "evidence": ["回答正文中的 DOI 字符串"],
                }
            ],
            "notes": [],
        }

    _patch_complete_json(monkeypatch, fake)
    res = er.review_answer(
        "Y 材料的导电率？",
        "据 DOI 10.9999/xxxx 报道，Y 材料导电率为 100 S/cm。",
        [],
        settings=_Settings(),
    )
    assert res is not None
    assert res["status"] == "failure"
    assert res["unsupported_count"] == 1  # 5.8 blocking 计入 unsupported
    assert res["suggestion"] is not None and "blocking" in res["suggestion"]
    assert any("[5.8·blocking]" in n for n in res["notes"])
    assert len(res["findings"]) == 1  # 原始 findings 保留


def test_llm_json_failure_falls_back_to_heuristic(monkeypatch: pytest.MonkeyPatch):
    """complete_json 解析失败（返回 None）→ 回退启发式，不炸主流程。"""
    _patch_complete_json(monkeypatch, lambda prompt: None)
    _patch_heuristic(monkeypatch)
    res = er.review_answer("q", "普通回答", [], settings=_Settings())
    assert res is not None
    assert res["status"] == "pass"
    assert res["unsupported_count"] == 0  # 启发式字段形状


def test_llm_invalid_schema_falls_back_to_heuristic(monkeypatch: pytest.MonkeyPatch):
    """LLM 输出非法 schema → 视为失败，回退启发式。"""
    _patch_complete_json(monkeypatch, lambda prompt: {"status": "weird"})
    _patch_heuristic(monkeypatch)
    res = er.review_answer("q", "普通回答", [], settings=_Settings())
    assert res is not None
    assert res["status"] == "pass"


def test_llm_exception_fails_open(monkeypatch: pytest.MonkeyPatch):
    """complete_json 抛异常 → fail-open，回退启发式。"""

    def boom(prompt: str):
        raise RuntimeError("llm down")

    _patch_complete_json(monkeypatch, boom)
    _patch_heuristic(monkeypatch)
    res = er.review_answer("q", "普通回答", [], settings=_Settings())
    assert res is not None
    assert res["status"] == "pass"


def test_flag_off_does_not_call_llm(monkeypatch: pytest.MonkeyPatch):
    """evidence_reviewer_llm_enabled=False → 不调 LLM。"""

    def boom(prompt: str):
        raise AssertionError("LLM 不应被调用")

    _patch_complete_json(monkeypatch, boom)
    _patch_heuristic(monkeypatch)
    res = er.review_answer("q", "普通回答", [], settings=_SettingsLLMOff())
    assert res is not None
    assert res["status"] == "pass"


def test_reviewer_disabled_returns_none(monkeypatch: pytest.MonkeyPatch):
    def boom(prompt: str):
        raise AssertionError("不应被调用")

    _patch_complete_json(monkeypatch, boom)
    assert (
        er.review_answer("q", "a", [], settings=_SettingsOff()) is None
    )
    assert er.review_answer("q", "", [], settings=_Settings()) is None


def test_citations_capped_at_20_and_snippet_truncated(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake(prompt: str):
        captured["prompt"] = prompt
        return {"status": "pass", "findings": [], "notes": []}

    _patch_complete_json(monkeypatch, fake)
    cites = [
        {"title": f"t{i}", "doi": f"10.1/{i}", "snippet": "x" * 500}
        for i in range(25)
    ]
    res = er.review_answer_llm("q", "a", cites, settings=_Settings())
    assert res is not None and res["status"] == "pass"
    prompt = captured["prompt"]
    assert "[20]" in prompt
    assert "[21]" not in prompt
    assert "x" * 301 not in prompt  # snippet 截断到 300


def test_review_answer_llm_empty_answer_returns_none():
    assert er.review_answer_llm("q", "  ", [], settings=_Settings()) is None


def test_severity_normalized_to_minor(monkeypatch: pytest.MonkeyPatch):
    """非法 severity → minor；notes 非 list → []。"""

    def fake(prompt: str):
        return {
            "status": "warning",
            "findings": [
                {
                    "rule": "5.4-3",
                    "severity": "critical",  # 非法值
                    "title": "归因存疑",
                    "detail": "d",
                    "evidence": "not-a-list",
                }
            ],
            "notes": "not-a-list",
        }

    _patch_complete_json(monkeypatch, fake)
    res = er.review_answer("q", "a", [], settings=_Settings())
    assert res["status"] == "warning"
    assert res["findings"][0]["severity"] == "minor"
    assert res["findings"][0]["evidence"] == []
    assert res["weak_count"] == 0  # minor 不计入 weak
