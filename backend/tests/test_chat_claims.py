"""P3: chat_claims 共享 executor 测试(2026-09-04)。"""
from __future__ import annotations

import time

from app.services import chat_claims as cc


def test_claim_executor_is_singleton():
    a = cc._claim_executor()
    b = cc._claim_executor()
    assert a is b
    assert a._max_workers == 2


def test_submit_quick_success(monkeypatch):
    """正常路径: LLM 验证快速返回 → 结果透传(不触发 offline)。"""
    def fake_verify(question, claims, sources):
        return "ok"

    monkeypatch.setattr(cc, "verify_claims_llm", fake_verify)
    ex = cc._claim_executor()
    fut = ex.submit(fake_verify, "q", [], [])
    assert fut.result(timeout=5) == "ok"


def test_slow_claim_does_not_spawn_threads_per_call(monkeypatch):
    """慢验证(>12s 超时)连续触发时线程有界(共享池 max_workers=2),
    不再每问新建孤儿线程。不实际等 12s —— 验证池为单例且线程名前缀固定。"""
    import threading

    ex = cc._claim_executor()
    assert ex is cc._claim_executor()
    assert ex._max_workers == 2
    names = {t.name for t in threading.enumerate()}
    assert any(n.startswith("claim-verify") for n in names)


def test_exception_falls_back_offline(monkeypatch):
    """verify_claims_llm 抛异常 → offline 验证兜底(共享池下行为不变)。"""
    calls = {"offline": 0}

    def fake_verify(question, claims, sources):
        raise RuntimeError("llm down")

    def fake_offline(c, sources):
        calls["offline"] += 1
        verdict = type("V", (), {"verdict": "supported", "text": c,
                                 "evidence_indices": [0]})()
        return verdict

    monkeypatch.setattr(cc, "verify_claims_llm", fake_verify)
    monkeypatch.setattr(cc, "verify_claim_offline", fake_offline)
    monkeypatch.setattr(cc, "get_settings",
                        lambda: type("S", (), {"chat_claim_check_enabled": True})())

    out = cc.build_sourced_claims("问题?", "这是第一句。这是第二句。", [])
    assert calls["offline"] == 2
    assert len(out) == 2
    assert all(c.status == "supported" for c in out)
