"""B-7 regression: chat 两条路径不能吞掉 ReviewerModelError。

Wave 5 显错契约：配置 evidence_reviewer_model 后 reviewer 失败必须抛
ReviewerModelError。chat 必须返回显式的 reviewer error 状态
（evidence_reviewer={"status": "error", "reviewer_error": ...}），
绝不能看起来"审过"（pass / None 静默）。
"""
from __future__ import annotations

from types import SimpleNamespace

import app.api.chat as chat_mod
from app.services.evidence_reviewer import ReviewerModelError


def _raise_reviewer_error(question, answer, evidence, *, settings):
    raise ReviewerModelError("reviewer 模型 'mini' 调用失败: timeout")


def _boom(question, answer, evidence, *, settings):
    raise RuntimeError("llm not configured")


def test_run_evidence_review_surfaces_model_error(monkeypatch):
    """ReviewerModelError → 显错 payload：status=error + reviewer_error。"""
    import app.services.evidence_reviewer as er

    monkeypatch.setattr(er, "review_answer", _raise_reviewer_error)
    out = chat_mod._run_evidence_review("q", "a", [], SimpleNamespace())
    assert isinstance(out, dict)
    assert out["status"] == "error"
    assert "timeout" in out["reviewer_error"]
    assert chat_mod._reviewer_failed(out)


def test_run_evidence_review_other_errors_stay_failopen(monkeypatch):
    """非 ReviewerModelError（未配置专用模型）→ 保持 fail-open 返回 None。"""
    import app.services.evidence_reviewer as er

    monkeypatch.setattr(er, "review_answer", _boom)
    out = chat_mod._run_evidence_review("q", "a", [], SimpleNamespace())
    assert out is None
    assert not chat_mod._reviewer_failed(out)
    assert not chat_mod._reviewer_failed(None)


def test_finalize_evidence_fields_reviewer_error_no_fix_loop(monkeypatch):
    """stream 路径：reviewer 显错时客户端拿到 error 状态，且不进 fix-loop。"""
    import app.services.evidence_reviewer as er
    import app.services.evidence_synthesis as es
    import app.services.reviewer_fix_loop as rfl

    monkeypatch.setattr(es, "evidence_mode_active", lambda mode, settings: True)
    monkeypatch.setattr(
        es,
        "postprocess_evidence_answer",
        lambda answer, settings=None: (answer, {}),
    )
    monkeypatch.setattr(er, "review_answer", _raise_reviewer_error)

    def _must_not_run(**kwargs):
        raise AssertionError("fix-loop must not run when reviewer is in error")

    monkeypatch.setattr(rfl, "run_fix_loop", _must_not_run)

    # v14-1: 6 元组解包（a61c5d1 漏改）
    answer, doi_results, reviewer, reviewer_fix, citation_expand, citations = (
        chat_mod._finalize_evidence_fields(
            "q?",
            "原始答案",
            [],
            settings=SimpleNamespace(),
            mode="evidence",
            selected_skills=[],
            project_id="p1",
        )
    )
    assert citations == [], "reviewer 出错不跑 fix-loop，citations 应原样返回"
    assert answer == "原始答案"
    assert reviewer_fix is None
    assert isinstance(reviewer, dict)
    assert reviewer["status"] == "error"
    assert "timeout" in reviewer["reviewer_error"]
