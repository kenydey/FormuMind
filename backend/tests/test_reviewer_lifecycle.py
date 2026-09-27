"""Tests for W2-8: reviewer hardening (P1-11/12/13 + P1-27)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.services import evidence_reviewer as er
from app.services import reviewer_fix_loop as rfl


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rfl, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(rfl, "_AUTO_STATE", {})
    monkeypatch.setattr(rfl, "_FIX_LOOP_DEPTH", 0)
    return tmp_path


class _Off:
    auto_audit_enabled = False


class _On:
    auto_audit_enabled = True
    evidence_reviewer_enabled = True
    evidence_reviewer_fix_loop_enabled = True


# ---------------------------------------------------------------- P1-11


def test_tool_whitelist_is_empty_frozenset():
    assert isinstance(er.REVIEWER_ALLOWED_TOOLS, frozenset)
    assert len(er.REVIEWER_ALLOWED_TOOLS) == 0


def test_tool_boundary_rejects_any_tool():
    with pytest.raises(PermissionError):
        er.assert_reviewer_tool_boundary("web_search")
    with pytest.raises(PermissionError):
        er.assert_reviewer_tool_boundary("mcp__anything")


def test_tool_boundary_no_arg_is_noop():
    er.assert_reviewer_tool_boundary()  # 不抛错：声明流水线不经过工具分发


def test_review_answer_entry_asserts_boundary(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    def _recorder(tool_name=None):
        calls["n"] += 1

    monkeypatch.setattr(er, "assert_reviewer_tool_boundary", _recorder)
    # 开关关闭 → 直接 None，但入口断言必须已执行
    assert er.review_answer("q", "a", [], settings=object()) is None
    assert calls["n"] == 1


# ---------------------------------------------------------------- P1-27


def test_rubric_prompt_has_antiself_dealing_clause():
    assert "P1-27" in er.REVIEWER_RUBRIC_PROMPT
    assert "防自证" in er.REVIEWER_RUBRIC_PROMPT
    assert "循环论证" in er.REVIEWER_RUBRIC_PROMPT
    # Wave 1 各节仍在
    for marker in ["§5.7", "§5.8", "§5.4", "§5.9", "§5.11"]:
        assert marker in er.REVIEWER_RUBRIC_PROMPT, marker


# ---------------------------------------------------------------- P1-12


def test_auto_review_disabled_by_default():
    assert rfl.maybe_auto_review("t1", question="q", answer="a", citations=[],
                                 settings=_Off()) is None
    assert rfl.maybe_auto_review("t1", question="q", answer="a", citations=[],
                                 settings=object()) is None  # 无该属性 → 关


def test_auto_review_fires_and_is_idempotent_per_turn(
    tmp_data, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        er, "review_answer",
        lambda q, a, c, settings=None: {"status": "pass", "notes": []},
    )
    meta = rfl.maybe_auto_review("t1", question="q", answer="a", citations=[],
                                 settings=_On(), session_id="s1")
    assert meta is not None and meta["turn_id"] == "t1"
    # 同一 turn_id 第二次 → 幂等跳过
    assert rfl.maybe_auto_review("t1", question="q", answer="a", citations=[],
                                 settings=_On(), session_id="s1") is None


def test_auto_review_debounce_last_turn_wins(tmp_data, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        er, "review_answer",
        lambda q, a, c, settings=None: {"status": "pass", "notes": []},
    )
    results: dict[str, object] = {}
    started = threading.Event()

    def _call(turn: str):
        if turn == "t1":
            started.set()
        results[turn] = rfl.maybe_auto_review(
            turn, question="q", answer="a", citations=[],
            settings=_On(), session_id="s2",
        )

    th = threading.Thread(target=_call, args=("t1",))
    th.start()
    assert started.wait(timeout=5)
    # 等 t1 进入防抖睡眠后再发 t2（轮询确认 pending 已置位）
    deadline = time.time() + 5
    while rfl._AUTO_STATE.get("s2", {}).get("pending") != "t1" and time.time() < deadline:
        time.sleep(0.005)
    _call("t2")
    th.join(timeout=5)
    assert results["t1"] is None  # 被更新的 turn 取代
    assert results["t2"] is not None and results["t2"]["turn_id"] == "t2"


def test_auto_review_suppressed_during_fix_loop(tmp_data, monkeypatch: pytest.MonkeyPatch):
    entered = threading.Event()
    release = threading.Event()

    def _slow_repair(q, auditor):
        entered.set()
        assert release.wait(timeout=10)
        return "fixed"

    monkeypatch.setattr(
        er, "review_answer",
        lambda q, a, c, settings=None: {"status": "pass", "notes": []},
    )
    review = {"status": "failure", "notes": ["n1"]}
    th = threading.Thread(
        target=rfl.run_fix_loop,
        kwargs=dict(question="q", answer="a", citations=[], review=review,
                    settings=_On(), repair_fn=_slow_repair, max_rounds=2),
    )
    th.start()
    assert entered.wait(timeout=10)
    # fix loop 执行期间 → 抑制
    assert rfl.maybe_auto_review("tx", question="q", answer="a", citations=[],
                                 settings=_On(), session_id="s3") is None
    release.set()
    th.join(timeout=10)
    # fix loop 结束后恢复
    meta = rfl.maybe_auto_review("ty", question="q", answer="a", citations=[],
                                 settings=_On(), session_id="s3")
    assert meta is not None and meta["turn_id"] == "ty"


def test_auto_review_runs_fix_on_nonpass(tmp_data, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        er, "review_answer",
        lambda q, a, c, settings=None: {"status": "failure", "notes": ["n1"]},
    )
    meta = rfl.maybe_auto_review(
        "t9", question="q", answer="a", citations=[], settings=_On(),
        repair_fn=lambda q, a_: "fixed", session_id="s4",
    )
    assert meta is not None
    assert meta["review"]["status"] == "failure"
    assert meta["fix"] is not None  # 触发了 fix loop


# ---------------------------------------------------------------- P1-13


def test_review_run_lifecycle_persisted(tmp_data):
    run = rfl.new_review_run(session_key="k1", project_id="p1")
    assert run["status"] == "running" and run["outcome"] == "null"
    rfl.save_review_run(run)
    done = rfl.finish_review_run(run, outcome="pass")
    assert done["status"] == "complete" and done["outcome"] == "pass"
    assert done["finished_at"] is not None
    rfl.save_review_run(done)
    loaded = rfl.load_review_run(run["run_id"])
    assert loaded is not None and loaded["status"] == "complete"
    assert loaded["outcome"] == "pass"
    assert rfl.load_review_run("no-such-run") is None


def test_fix_loop_persists_complete_run(tmp_data, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        er, "review_answer",
        lambda q, a, c, settings=None: {"status": "pass", "notes": []},
    )
    review = {"status": "failure", "notes": ["n1"]}
    ans, meta = rfl.run_fix_loop(
        question="q", answer="a", citations=[], review=review,
        settings=_On(), repair_fn=lambda q, a_: "fixed", max_rounds=2,
    )
    assert ans == "fixed"
    assert meta["run_id"]
    run = rfl.load_review_run(meta["run_id"])
    assert run is not None
    assert run["status"] == "complete"
    assert run["outcome"] == "pass"
    assert meta["run_status"] == "complete" and meta["run_outcome"] == "pass"


def test_fix_loop_final_nonpass_outcome_flagged(tmp_data, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        er, "review_answer",
        lambda q, a, c, settings=None: {"status": "failure", "notes": ["n1"]},
    )
    review = {"status": "failure", "notes": ["n1"]}
    _, meta = rfl.run_fix_loop(
        question="q", answer="a", citations=[], review=review,
        settings=_On(), repair_fn=lambda q, a_: "still bad", max_rounds=1,
    )
    run = rfl.load_review_run(meta["run_id"])
    assert run["status"] == "complete"
    assert run["outcome"] == "flagged"


def test_fix_loop_never_raises_and_persists_error(tmp_data, monkeypatch: pytest.MonkeyPatch):
    def _boom(key, claims):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(rfl, "save_dispositions", _boom)
    monkeypatch.setattr(
        er, "review_answer",
        lambda q, a, c, settings=None: {"status": "pass", "notes": []},
    )
    review = {"status": "failure", "notes": ["n1"]}
    # 不得抛错
    ans, meta = rfl.run_fix_loop(
        question="q", answer="a", citations=[], review=review,
        settings=_On(), repair_fn=lambda q, a_: "fixed", max_rounds=1,
    )
    assert ans == "a"  # fail-open：返回原答案
    assert meta["status"] == "error"
    run = rfl.load_review_run(meta["run_id"])
    assert run is not None and run["status"] == "error"
