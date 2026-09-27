"""Tests for W1-11: MCP retry (timeout/connection only, exponential backoff)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import mcp_client


@pytest.fixture()
def fake_session(monkeypatch: pytest.MonkeyPatch):
    """替身 _StdioSession：start/close 为空操作，_request 按 side_effect 行为。

    返回 (state, sleeps)：state["calls"] 为 _request 调用次数，
    sleeps 记录 _sleep 的延迟序列。调用 fake_session.plan([...]) 设置行为。
    """
    state = {"calls": 0, "effects": []}
    sleeps: list[float] = []

    def fake_start(self, timeout_s: float = 8.0) -> None:
        return None

    def fake_close(self) -> None:
        return None

    def fake_request(self, method, params, *, timeout_s: float = 8.0):
        state["calls"] += 1
        effects = state["effects"]
        item = effects[min(state["calls"] - 1, len(effects) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(mcp_client._StdioSession, "start", fake_start)
    monkeypatch.setattr(mcp_client._StdioSession, "close", fake_close)
    monkeypatch.setattr(mcp_client._StdioSession, "_request", fake_request)
    monkeypatch.setattr(mcp_client, "_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        mcp_client,
        "_prefs_servers",
        lambda: [
            {
                "id": "s1",
                "command": "true",
                "args": [],
                "enabled": True,
                "env": {},
                "transport": "stdio",
            }
        ],
    )

    class Planner:
        def plan(self, effects: list) -> None:
            state["effects"] = list(effects)

    return Planner(), state, sleeps


def _settings(retries: int = 2, backoff_s: float = 0.5):
    return SimpleNamespace(mcp_tool_retries=retries, mcp_retry_backoff_s=backoff_s)


def test_with_retry_unit():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("t")
        return "ok"

    sleeps: list[float] = []
    orig_sleep = mcp_client._sleep
    mcp_client._sleep = lambda s: sleeps.append(s)  # noqa: E731
    try:
        assert mcp_client._with_retry(flaky, retries=2, backoff_s=0.5) == "ok"
    finally:
        mcp_client._sleep = orig_sleep
    assert calls["n"] == 3
    assert sleeps == [0.5, 1.5]  # 指数退避 0.5s → 1.5s


def test_with_retry_business_error_not_retried():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("json-rpc error")

    with pytest.raises(RuntimeError):
        mcp_client._with_retry(boom, retries=2, backoff_s=0.5)
    assert calls["n"] == 1


def test_with_retry_exhausted_raises_last():
    calls = {"n": 0}

    def always_timeout():
        calls["n"] += 1
        raise TimeoutError("t")

    orig_sleep = mcp_client._sleep
    mcp_client._sleep = lambda s: None  # noqa: E731
    try:
        with pytest.raises(TimeoutError):
            mcp_client._with_retry(always_timeout, retries=2, backoff_s=0.5)
    finally:
        mcp_client._sleep = orig_sleep
    assert calls["n"] == 3  # 1 初次 + 2 重试


def test_call_tool_retries_then_succeeds(fake_session):
    planner, state, sleeps = fake_session
    planner.plan([TimeoutError("slow"), {"content": [{"text": "42"}]}])
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is True
    assert out["result"] == {"content": [{"text": "42"}]}
    assert state["calls"] == 2
    assert sleeps == [0.5]


def test_call_tool_business_error_not_retried(fake_session):
    planner, state, sleeps = fake_session
    planner.plan([RuntimeError("json-rpc: method not found")])
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is False
    assert "method not found" in out["error"]
    assert state["calls"] == 1  # 业务错误不重试
    assert sleeps == []


def test_call_tool_retry_exhausted_returns_error(fake_session):
    planner, state, sleeps = fake_session
    planner.plan([TimeoutError("slow")])
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is False
    assert "slow" in out["error"]
    assert state["calls"] == 3  # 1 初次 + 2 重试
    assert sleeps == [0.5, 1.5]


def test_call_tool_connection_error_retried(fake_session):
    planner, state, sleeps = fake_session
    planner.plan([ConnectionError("broken pipe"), {"content": []}])
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is True
    assert state["calls"] == 2


def test_probe_server_returns_descriptors_and_retries(fake_session):
    planner, state, sleeps = fake_session
    planner.plan(
        [
            TimeoutError("slow"),
            {
                "tools": [
                    {
                        "name": "search",
                        "description": "搜索化合物",
                        "inputSchema": {
                            "type": "object",
                            "required": ["q"],
                            "properties": {"q": {"type": "string"}},
                        },
                    }
                ]
            },
        ]
    )
    server = {
        "id": "s1",
        "command": "true",
        "args": [],
        "enabled": True,
        "env": {},
        "transport": "stdio",
    }
    out = mcp_client.probe_server(server, settings=_settings())
    assert out["ok"] is True
    assert out["names"] == ["search"]  # 兼容字段
    assert out["tools"] == [
        {
            "name": "search",
            "description": "搜索化合物",
            "inputSchema": {
                "type": "object",
                "required": ["q"],
                "properties": {"q": {"type": "string"}},
            },
        }
    ]
    assert state["calls"] == 2  # 首次超时后重试成功
    assert sleeps == [0.5]


def test_probe_server_non_stdio_no_session():
    out = mcp_client.probe_server({"id": "x", "transport": "http"})
    assert out["ok"] is False
    assert out["tools"] == []
    assert out["names"] == []
