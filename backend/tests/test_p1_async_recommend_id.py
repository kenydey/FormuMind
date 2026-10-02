"""P1-1: 异步推荐任务必须把 recommend_id 透传到 research 结果。

回归目标：worker 的 research 字典缺 recommend_id → 前端 lastRecommendId
恒为 null → 采纳按钮静默禁用、C-8 分子恒为 0。
"""
from __future__ import annotations

import types

import pytest


class _FakeTracker:
    def __init__(self, *a, **k):
        pass

    def emit(self, *a, **k):
        pass

    def thought(self, *a, **k):
        pass

    def finish(self, *a, **k):
        pass


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def model_dump(self):
        return dict(self._payload)


def _run_task(monkeypatch, resp_payload: dict):
    from app.worker import tasks as wt

    captured: dict = {}

    def fake_recommend(body):
        return _FakeResp(resp_payload)

    monkeypatch.setattr(
        "app.api.formulations.recommend_formulations", fake_recommend
    )
    monkeypatch.setattr(wt, "ThinkingTracker", _FakeTracker)
    monkeypatch.setattr(wt, "dispatch_kb_ingest", lambda *a, **k: "kb-task-1")
    monkeypatch.setattr(
        wt, "persist_result", lambda task_id, result, failed=False: captured.update(result=result, failed=failed)
    )
    monkeypatch.setattr(wt, "_persist_terminal", lambda *a, **k: None)

    fake_self = types.SimpleNamespace(request=types.SimpleNamespace(id="task-1"))
    # celery bind=True 任务：用 apply 走 eager 路径，request 上下文自动就绪。
    res = wt.run_recommend_task.apply(
        args=(
            {"requirement": {"domain": "anticorrosion_coating"}, "n": 2, "sources": []},
        ),
        task_id="task-1",
    )
    out = res.get()
    return out, captured


def test_worker_recommend_result_carries_recommend_id(monkeypatch):
    out, captured = _run_task(
        monkeypatch,
        {
            "formulas": [],
            "scored": [],
            "engine": "llm",
            "tradeoff": None,
            "warnings": [],
            "grounded_evidence": [],
            "recommend_id": "rec-async-123",
        },
    )
    research = out["research"]
    assert research["recommend_id"] == "rec-async-123"
    # persist 的也是同一份
    assert captured["result"]["research"]["recommend_id"] == "rec-async-123"
    assert captured["failed"] is False


def test_worker_recommend_id_missing_stays_none_not_crash(monkeypatch):
    """上游没给 recommend_id 时透传 None（不崩，前端保持禁用）。"""
    out, _ = _run_task(
        monkeypatch,
        {
            "formulas": [],
            "scored": [],
            "engine": "llm",
            "tradeoff": None,
            "warnings": [],
            "grounded_evidence": [],
        },
    )
    assert out["research"]["recommend_id"] is None
