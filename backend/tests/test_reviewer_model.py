"""Tests for P1-19 reviewer 独立小模型 (W5-2).

- llm._call_llm / complete_json 支持 per-caller model 覆盖；model=None 走全局配置（向后兼容）
- evidence_reviewer.review_answer_llm 传入 settings.evidence_reviewer_model
- new_review_run 的 run dict 记录 model 标签
- 配置了小模型时调用失败 → 抛 ReviewerModelError（不静默回退）
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import evidence_reviewer as er
from app.services import llm as llm_mod
from app.services import reviewer_fix_loop as rfl


# ---------------------------------------------------------------------------
# llm.complete_json / _call_llm 的 model 透传
# ---------------------------------------------------------------------------


def test_complete_json_passes_model_to_call_llm(monkeypatch: pytest.MonkeyPatch):
    calls: dict = {}

    def fake_call_llm(prompt: str, *, model=None):
        calls["model"] = model
        return '{"status": "pass"}'

    monkeypatch.setattr(llm_mod, "_call_llm", fake_call_llm)
    res = llm_mod.complete_json('{"x": 1}', model="mini-reviewer")
    assert calls["model"] == "mini-reviewer"
    assert res == {"status": "pass"}


def test_complete_json_model_none_keeps_default(monkeypatch: pytest.MonkeyPatch):
    calls: dict = {}

    def fake_call_llm(prompt: str, *, model=None):
        calls["model"] = model
        return '{"status": "pass"}'

    monkeypatch.setattr(llm_mod, "_call_llm", fake_call_llm)
    llm_mod.complete_json('{"x": 1}')
    assert calls["model"] is None, "model 未传时必须为 None（走全局配置）"


def test_call_llm_model_override_bypasses_global(monkeypatch: pytest.MonkeyPatch):
    """_call_llm(model=...) 传入 provider 层，不取全局 llm_model。"""
    captured: dict = {}

    class _Settings:
        llm_provider = "openai"
        llm_model = "global-main-model"
        llm_max_tokens = 100
        llm_base_url = ""

        def get_active_api_key(self):
            return "test-key"

    monkeypatch.setattr(llm_mod, "get_settings", lambda: _Settings())

    def fake_openai(prompt, api_key, model, max_tokens, base_url, disable_thinking=False):
        captured["model"] = model
        return "ok"

    monkeypatch.setattr(llm_mod, "_complete_openai_compatible", fake_openai)
    llm_mod._call_llm("hi", model="mini-reviewer")
    assert captured["model"] == "mini-reviewer"


def test_call_llm_model_none_uses_global(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    class _Settings:
        llm_provider = "openai"
        llm_model = "global-main-model"
        llm_max_tokens = 100
        llm_base_url = ""

        def get_active_api_key(self):
            return "test-key"

    monkeypatch.setattr(llm_mod, "get_settings", lambda: _Settings())

    def fake_openai(prompt, api_key, model, max_tokens, base_url, disable_thinking=False):
        captured["model"] = model
        return "ok"

    monkeypatch.setattr(llm_mod, "_complete_openai_compatible", fake_openai)
    llm_mod._call_llm("hi")
    assert captured["model"] == "global-main-model"


# ---------------------------------------------------------------------------
# reviewer_model_name 解析
# ---------------------------------------------------------------------------


def test_reviewer_model_name_empty_and_missing():
    assert er.reviewer_model_name(SimpleNamespace()) is None
    assert er.reviewer_model_name(SimpleNamespace(evidence_reviewer_model="")) is None
    assert er.reviewer_model_name(SimpleNamespace(evidence_reviewer_model="  ")) is None
    assert er.reviewer_model_name(SimpleNamespace(evidence_reviewer_model="mini")) == "mini"


# ---------------------------------------------------------------------------
# review_answer_llm 接线：传入 model；失败时明确报错
# ---------------------------------------------------------------------------


def _settings_with_model(model: str | None):
    return SimpleNamespace(
        evidence_reviewer_enabled=True,
        evidence_reviewer_llm_enabled=True,
        evidence_reviewer_model=model or "",
    )


def test_review_answer_llm_passes_configured_model(monkeypatch: pytest.MonkeyPatch):
    calls: dict = {}

    def fake(prompt: str, *, model=None):
        calls["model"] = model
        return {"status": "pass", "findings": [], "notes": []}

    monkeypatch.setattr("app.services.llm.complete_json", fake)
    res = er.review_answer_llm("q", "a", [], settings=_settings_with_model("mini-reviewer"))
    assert calls["model"] == "mini-reviewer"
    assert res is not None and res.get("status") == "pass"


def test_review_answer_llm_model_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch):
    calls: dict = {}

    def fake(prompt: str, *, model=None):
        calls["model"] = model
        return {"status": "pass", "findings": [], "notes": []}

    monkeypatch.setattr("app.services.llm.complete_json", fake)
    res = er.review_answer_llm("q", "a", [], settings=_settings_with_model(None))
    assert calls["model"] is None
    assert res is not None


def test_review_answer_llm_model_failure_raises(monkeypatch: pytest.MonkeyPatch):
    """配置小模型 + 调用抛错 → ReviewerModelError，不静默回退。"""

    def fake(prompt: str, *, model=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.llm.complete_json", fake)
    with pytest.raises(er.ReviewerModelError, match="mini-reviewer"):
        er.review_answer_llm("q", "a", [], settings=_settings_with_model("mini-reviewer"))


def test_review_answer_llm_model_invalid_output_raises(monkeypatch: pytest.MonkeyPatch):
    """配置小模型 + 返回非法 → ReviewerModelError（避免"以为审了"）。"""

    def fake(prompt: str, *, model=None):
        return {"garbage": True}

    monkeypatch.setattr("app.services.llm.complete_json", fake)
    with pytest.raises(er.ReviewerModelError, match="mini-reviewer"):
        er.review_answer_llm("q", "a", [], settings=_settings_with_model("mini-reviewer"))


def test_review_answer_llm_no_model_fail_open(monkeypatch: pytest.MonkeyPatch):
    """未配置小模型时保持 fail-open（返回 None，由调用方回退启发式）。"""

    def fake(prompt: str, *, model=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.llm.complete_json", fake)
    res = er.review_answer_llm("q", "a", [], settings=_settings_with_model(None))
    assert res is None


# ---------------------------------------------------------------------------
# run dict 的 model 标签
# ---------------------------------------------------------------------------


def test_new_review_run_records_model_tag():
    run = rfl.new_review_run(project_id="p1", model="mini-reviewer")
    assert run["model"] == "mini-reviewer"


def test_new_review_run_model_defaults_none():
    run = rfl.new_review_run(project_id="p1")
    assert run["model"] is None
