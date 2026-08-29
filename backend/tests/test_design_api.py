"""Inverse-design API 路由层测试：请求校验、异步提交契约、broker 降级。

test_inverse_design.py 已覆盖服务层搜索行为；本文件只测 HTTP 契约：
202 接受并透传 payload、422 参数校验、503 broker 不可达降级。
"""
from __future__ import annotations

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _body() -> dict:
    return {
        "requirement": {"domain": "anticorrosion_coating"},
        "targets": {"hard": [], "soft": []},
        "population": 48,
        "generations": 30,
        "seed_with_llm": False,
    }


# ── 正常提交：202 + payload 透传 ─────────────────────────────────────────────


def test_inverse_accepts_valid_body(monkeypatch):
    captured: dict = {}

    def fake_submit(task, payload, kind, *, outbox_id=None, owner_id=None):
        captured["task"] = task
        captured["payload"] = payload
        captured["kind"] = kind
        return JSONResponse(content={"task_id": "fake", "kind": kind}, status_code=202)

    monkeypatch.setattr("app.api.design.submit", fake_submit)
    monkeypatch.setattr("app.api.design.enqueue_outbox", lambda op, payload: "outbox-1")
    r = client.post("/api/design/inverse", json=_body())
    assert r.status_code == 202
    assert captured["kind"] == "inverse_design"
    assert captured["payload"]["requirement"]["domain"] == "anticorrosion_coating"
    assert captured["payload"]["population"] == 48
    assert captured["payload"]["generations"] == 30
    assert captured["payload"]["seed_with_llm"] is False


# ── 422 参数校验 ─────────────────────────────────────────────────────────────


def test_inverse_rejects_population_below_min():
    body = _body()
    body["population"] = 4
    r = client.post("/api/design/inverse", json=body)
    assert r.status_code == 422


def test_inverse_rejects_generations_zero():
    body = _body()
    body["generations"] = 0
    r = client.post("/api/design/inverse", json=body)
    assert r.status_code == 422


def test_inverse_requires_requirement():
    body = _body()
    del body["requirement"]
    r = client.post("/api/design/inverse", json=body)
    assert r.status_code == 422


# ── broker 不可达降级：503 而非 500 ──────────────────────────────────────────


def test_inverse_broker_down_503(monkeypatch):
    monkeypatch.setattr("app.api._dispatch.broker_reachable", lambda: False)
    monkeypatch.setattr("app.api.design.enqueue_outbox", lambda op, payload: None)
    r = client.post("/api/design/inverse", json=_body())
    assert r.status_code == 503
    assert "Redis" in r.text
