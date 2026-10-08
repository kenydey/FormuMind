"""Tests for evidence reviewer fix-loop."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import reviewer_fix_loop as rfl


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rfl, "_data_root", lambda: tmp_path)
    return tmp_path


class _Settings:
    evidence_reviewer_fix_loop_enabled = True
    evidence_reviewer_enabled = True


def test_fix_loop_disabled_returns_none(tmp_data):
    class Off:
        evidence_reviewer_fix_loop_enabled = False

    ans, meta = rfl.run_fix_loop(
        question="q",
        answer="a",
        citations=[],
        review={"status": "failure", "notes": ["x"]},
        settings=Off(),
        repair_fn=lambda q, a: "fixed",
    )
    assert ans == "a"
    assert meta is None


def test_fix_loop_one_round_repair(tmp_data, monkeypatch):
    calls = {"n": 0}

    def fake_review(question, answer, citations, *, settings):
        if "fixed" in answer:
            return {"status": "pass", "notes": [], "suggestion": None}
        return {
            "status": "failure",
            "notes": ["1 条断言无据"],
            "suggestion": "收紧",
            "unsupported_count": 1,
            "weak_count": 0,
        }

    monkeypatch.setattr(
        "app.services.evidence_reviewer.review_answer",
        fake_review,
    )

    def repair(q, auditor):
        calls["n"] += 1
        assert "[Auditor]" in auditor
        return "fixed answer with [^1]"

    ans, meta = rfl.run_fix_loop(
        question="What temp?",
        answer="bad claim",
        citations=[],
        review={
            "status": "failure",
            "notes": ["1 条断言无据"],
            "suggestion": "收紧",
        },
        settings=_Settings(),
        repair_fn=repair,
        max_rounds=3,
    )
    assert calls["n"] == 1
    assert "fixed" in ans
    assert meta is not None
    assert meta["rounds"] == 1
    assert meta["status"] == "pass"


def test_fix_loop_unaddressed_after_max(tmp_data, monkeypatch):
    monkeypatch.setattr(
        "app.services.evidence_reviewer.review_answer",
        lambda *a, **k: {
            "status": "failure",
            "notes": ["still bad"],
            "suggestion": "x",
        },
    )
    n = {"i": 0}

    def repair(q, auditor):
        n["i"] += 1
        return f"attempt {n['i']}"

    ans, meta = rfl.run_fix_loop(
        question="q",
        answer="a0",
        citations=[],
        review={"status": "failure", "notes": ["still bad"], "suggestion": "x"},
        settings=_Settings(),
        repair_fn=repair,
        max_rounds=2,
        project_id="proj-x",
    )
    assert meta is not None
    assert meta["rounds"] == 2
    assert meta.get("unaddressed")
    soft = tmp_data / "preflight" / "proj-x" / "evidence_soft.json"
    assert soft.is_file()


# ── B-8 回归：run_id 并发唯一 ────────────────────────────────────────────

def test_new_review_run_ids_unique_same_ms(monkeypatch):
    """同一毫秒、同一 session 并发建 run → run_id 必须全部唯一。"""
    import app.services.reviewer_fix_loop as rfl

    fixed = 1_700_000_000.123
    monkeypatch.setattr(rfl.time, "time", lambda: fixed)
    ids = [rfl.new_review_run(session_key="same-key")["run_id"] for _ in range(200)]
    assert len(set(ids)) == 200
    assert all(i.startswith("same-key-") for i in ids)


def test_concurrent_save_review_run_no_overwrite(tmp_data):
    """并发建 run + 落盘：审计文件数 == 建 run 数，无覆盖。"""
    import threading

    import app.services.reviewer_fix_loop as rfl

    n = 24
    created = []
    lock = threading.Lock()

    def _one():
        run = rfl.new_review_run(session_key="collide")
        rfl.save_review_run(run)
        with lock:
            created.append(run["run_id"])

    threads = [threading.Thread(target=_one) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(created)) == n
    files = list((tmp_data / "reviews" / "runs").glob("*.json"))
    assert len(files) == n


def test_run_fix_loop_rewrites_unique_run_id(tmp_data, monkeypatch):
    """run_fix_loop 内重写 run_id 必须带随机后缀（B-8 :452）。"""
    monkeypatch.setattr(
        "app.services.evidence_reviewer.review_answer",
        lambda *a, **k: {"status": "pass", "notes": []},
    )
    _, meta = rfl.run_fix_loop(
        question="q",
        answer="a",
        citations=[],
        review={"status": "warning", "notes": ["n"], "suggestion": "s"},
        settings=_Settings(),
        repair_fn=lambda q, a: "fixed",
        max_rounds=1,
        project_id="p",
    )
    assert meta["run_id"]
    assert len(meta["run_id"].rsplit("-", 1)[-1]) == 12  # uuid4 hex 前 12 位


# ── B-11 回归：_AUTO_STATE 有界（TTL + 容量上限 + 懒清理）─────────────────


@pytest.fixture()
def auto_state_isolated():
    saved = dict(rfl._AUTO_STATE)
    rfl._AUTO_STATE.clear()
    yield rfl._AUTO_STATE
    rfl._AUTO_STATE.clear()
    rfl._AUTO_STATE.update(saved)


def test_auto_state_ttl_prune(auto_state_isolated):
    import time as _time

    now = _time.time()
    rfl._AUTO_STATE["stale-scope"] = {
        "pending": None, "fired": set(), "ts": now - rfl._AUTO_STATE_TTL_S - 1
    }
    rfl._AUTO_STATE["fresh-scope"] = {
        "pending": None, "fired": set(), "ts": now
    }
    with rfl._AUTO_LOCK:
        rfl._auto_state_prune_locked(now)
    assert "stale-scope" not in rfl._AUTO_STATE
    assert "fresh-scope" in rfl._AUTO_STATE


def test_auto_state_cap_evicts_oldest(auto_state_isolated):
    import time as _time

    now = _time.time()
    for i in range(rfl._AUTO_STATE_CAP + 50):
        rfl._AUTO_STATE[f"scope-{i:05d}"] = {
            "pending": None, "fired": set(), "ts": now - i
        }
    with rfl._AUTO_LOCK:
        rfl._auto_state_prune_locked(now)
    assert len(rfl._AUTO_STATE) == rfl._AUTO_STATE_CAP
    # 淘汰的是最早活跃（ts 最小）的 scope
    assert "scope-00000" in rfl._AUTO_STATE
    assert f"scope-{rfl._AUTO_STATE_CAP + 49:05d}" not in rfl._AUTO_STATE


def test_maybe_auto_review_prunes_stale_scopes(auto_state_isolated):
    """懒清理真实触发路径：maybe_auto_review 进入时顺带清理过期 scope。"""
    import time as _time

    now = _time.time()
    rfl._AUTO_STATE["dead"] = {
        "pending": None, "fired": set(), "ts": now - rfl._AUTO_STATE_TTL_S - 10
    }

    class S:
        auto_audit_enabled = True

    rfl.maybe_auto_review(
        "turn-1",
        question="q",
        answer="a",
        citations=[],
        settings=S(),
        session_id="live",
    )
    assert "dead" not in rfl._AUTO_STATE
    assert "live" in rfl._AUTO_STATE
    assert rfl._AUTO_STATE["live"]["ts"] >= now


def test_finalize_evidence_fields_returns_rebound_citations(monkeypatch):
    """v13-1: 流式 fix-loop 重绑的 citations 必须随 6 元组返回。

    回归：旧 5 元组丢弃重绑结果，流式答案与引用卡片脱钩。
    """
    import app.api.chat as chat_mod

    old_citations = [{"title": "old1"}, {"title": "old2"}]
    new_citations = [{"title": "newA"}]
    new_answer = "修复后答案 [^1]"

    # _finalize_evidence_fields 内 from ..services.evidence_synthesis import
    import app.services.evidence_synthesis as es

    monkeypatch.setattr(es, "evidence_mode_active", lambda mode, settings: True)
    monkeypatch.setattr(
        es, "postprocess_evidence_answer", lambda answer, settings=None: (answer, {})
    )
    monkeypatch.setattr(
        chat_mod, "_run_evidence_review",
        lambda *a, **k: {"status": "failure", "notes": ["x"]},
    )
    monkeypatch.setattr(chat_mod, "_reviewer_failed", lambda r: False)
    monkeypatch.setattr(chat_mod, "_claims_evidence", lambda c: [])
    import app.services.reviewer_fix_loop as rfl

    monkeypatch.setattr(
        rfl, "run_fix_loop",
        lambda **kw: (new_answer, {"citations": new_citations, "findings": []}),
    )
    monkeypatch.setattr(chat_mod, "answer_question", lambda *a, **k: ("", []))
    monkeypatch.setattr(chat_mod, "_ensure_answer", lambda x: x)

    class S:
        pass

    out = chat_mod._finalize_evidence_fields(
        "q", "原答案 [^1][^2]", old_citations,
        settings=S(), mode="evidence", selected_skills=None,
    )
    assert len(out) == 6, "必须返回 6 元组（含 citations）"
    answer, _d, _r, _rf, _ce, citations = out
    assert answer == new_answer
    assert citations == new_citations, "重绑后的 citations 必须返回，不用旧表"


def test_finalize_evidence_fields_empty_rebind_not_dropped(monkeypatch):
    """v13-1: 重绑结果为空列表时也不得回退到旧表（is not None）。"""
    import app.api.chat as chat_mod
    import app.services.evidence_synthesis as es

    old_citations = [{"title": "old1"}]
    monkeypatch.setattr(es, "evidence_mode_active", lambda mode, settings: True)
    monkeypatch.setattr(
        es, "postprocess_evidence_answer", lambda answer, settings=None: (answer, {})
    )
    monkeypatch.setattr(
        chat_mod, "_run_evidence_review",
        lambda *a, **k: {"status": "failure", "notes": ["x"]},
    )
    monkeypatch.setattr(chat_mod, "_reviewer_failed", lambda r: False)
    monkeypatch.setattr(chat_mod, "_claims_evidence", lambda c: [])
    import app.services.reviewer_fix_loop as rfl

    monkeypatch.setattr(
        rfl, "run_fix_loop",
        lambda **kw: ("无引用答案", {"citations": [], "findings": []}),
    )
    monkeypatch.setattr(chat_mod, "answer_question", lambda *a, **k: ("", []))
    monkeypatch.setattr(chat_mod, "_ensure_answer", lambda x: x)

    class S:
        pass

    *_, citations = chat_mod._finalize_evidence_fields(
        "q", "原答案 [^1]", old_citations,
        settings=S(), mode="evidence", selected_skills=None,
    )
    assert citations == [], "空重绑结果必须生效，不能回退旧表"


def test_citation_coords_remap_drops_filtered(monkeypatch):
    """v25: citation_coords 共享函数 —— 过滤后映射 + 答案重写 + 删除被过滤标记。"""
    from types import SimpleNamespace

    from app.services import citation_coords as cc

    kb1 = SimpleNamespace(title="kb1")
    wiki = SimpleNamespace(title="wiki")
    kb2 = SimpleNamespace(title="kb2")
    citations = [kb1, wiki, kb2]
    monkeypatch.setattr(cc, "claims_evidence", lambda ev: [kb1, kb2])

    filtered, mapping = cc.claims_evidence_with_mapping(citations)
    assert filtered == [kb1, kb2]
    assert mapping == {0: 0, 2: 1}
    out = cc.remap_citation_numbers("A[^1]W[^2]B[^3]", mapping)
    assert out == "A[^1]WB[^2]"
