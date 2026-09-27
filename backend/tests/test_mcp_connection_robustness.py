"""Tests for W5-5 (P1-30): MCP 连接健壮性。

- 单 server 单 _StdioSession 缓存（复用同一 client 实例）
- 配置变更 → generation 递增（屏障），旧连接关闭重建
- 并发去重：同 server 并发 call_tool 只创建一次连接
- 失败分类上报：timeout / protocol / process_crash（error_kind）
- stderr/错误信息脱敏：疑似密钥不进日志与 error 返回
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.services import mcp_client


def _settings(retries: int = 2, backoff_s: float = 0.5):
    return SimpleNamespace(mcp_tool_retries=retries, mcp_retry_backoff_s=backoff_s)


@pytest.fixture()
def mcp_env(monkeypatch: pytest.MonkeyPatch):
    """替身环境：fake _StdioSession（计数创建/关闭），可变的 server 配置。"""
    mcp_client._reset_client_cache()
    created = {"n": 0}
    closed = {"n": 0}
    calls = {"n": 0}
    state = {"effects": []}
    config = {"args": []}

    def fake_start(self, timeout_s: float = 8.0) -> None:
        created["n"] += 1
        self._started = True

    def fake_close(self) -> None:
        closed["n"] += 1
        self._started = False

    def fake_request(self, method, params, *, timeout_s: float = 8.0):
        calls["n"] += 1
        effects = state["effects"]
        if not effects:
            return {"tools": []} if method == "tools/list" else {"content": []}
        item = effects[min(calls["n"] - 1, len(effects) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    def fake_prefs():
        return [
            {
                "id": "s1",
                "command": "true",
                "args": list(config["args"]),
                "enabled": True,
                "env": {},
                "transport": "stdio",
            }
        ]

    monkeypatch.setattr(mcp_client._StdioSession, "start", fake_start)
    monkeypatch.setattr(mcp_client._StdioSession, "close", fake_close)
    monkeypatch.setattr(mcp_client._StdioSession, "_request", fake_request)
    monkeypatch.setattr(mcp_client, "_prefs_servers", fake_prefs)
    monkeypatch.setattr(mcp_client, "_sleep", lambda s: None)

    env = SimpleNamespace(
        created=created, closed=closed, calls=calls, state=state, config=config
    )
    yield env
    mcp_client._reset_client_cache()


def test_same_server_reuses_single_client(mcp_env):
    out1 = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    out2 = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out1["ok"] is True
    assert out2["ok"] is True
    assert mcp_env.created["n"] == 1  # 只创建一次连接
    assert mcp_env.closed["n"] == 0  # 成功路径不关闭复用连接
    info = mcp_client._cache_info()
    assert info["s1"]["generation"] == 0
    assert info["s1"]["alive"] is True


def test_probe_and_call_share_cache(mcp_env):
    server = {
        "id": "s1",
        "command": "true",
        "args": [],
        "enabled": True,
        "env": {},
        "transport": "stdio",
    }
    probed = mcp_client.probe_server(server, settings=_settings())
    assert probed["ok"] is True
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is True
    assert mcp_env.created["n"] == 1  # probe 与 call 复用同一连接


def test_config_change_bumps_generation_and_rebuilds(mcp_env):
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is True
    assert mcp_env.created["n"] == 1
    assert mcp_client._cache_info()["s1"]["generation"] == 0

    mcp_env.config["args"] = ["--new-flag"]  # 配置变更
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is True
    assert mcp_env.created["n"] == 2  # 重建新连接
    assert mcp_env.closed["n"] == 1  # 旧连接被关闭
    info = mcp_client._cache_info()
    assert info["s1"]["generation"] == 1  # generation 屏障递增


def test_dead_session_rebuilt(mcp_env):
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is True
    assert mcp_env.created["n"] == 1
    # 模拟进程死亡
    with mcp_client._CLIENT_LOCK:
        mcp_client._CLIENT_CACHE["s1"]["session"]._started = False
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is True
    assert mcp_env.created["n"] == 2
    assert mcp_env.closed["n"] == 1


def test_concurrent_calls_share_one_inflight_connection(mcp_env, monkeypatch):
    real_sleep = time.sleep
    mcp_env.created["n"] = 0

    def slow_start(self, timeout_s: float = 8.0) -> None:
        mcp_env.created["n"] += 1
        real_sleep(0.3)  # 确保并发窗口
        self._started = True

    monkeypatch.setattr(mcp_client._StdioSession, "start", slow_start)
    monkeypatch.setattr(mcp_client, "_sleep", lambda s: real_sleep(min(s, 0.05)))

    errors: list[BaseException] = []

    def worker():
        try:
            out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
            assert out["ok"] is True
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    assert all(not t.is_alive() for t in threads)
    assert mcp_env.created["n"] == 1  # 并发去重：只创建一次


def test_process_crash_classified_evicted_and_retried(mcp_env):
    mcp_env.state["effects"] = [
        mcp_client._MCPProcessCrash("process gone"),
        {"content": [{"text": "recovered"}]},
    ]
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is True  # 崩溃后重建连接并重试成功
    assert out["result"] == {"content": [{"text": "recovered"}]}
    assert mcp_env.created["n"] == 2  # 旧连接逐出后重建
    assert mcp_env.closed["n"] == 1


def test_process_crash_exhausted_reports_kind(mcp_env):
    mcp_env.state["effects"] = [mcp_client._MCPProcessCrash("boom")]
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings(retries=1))
    assert out["ok"] is False
    assert out["error_kind"] == "process_crash"


def test_timeout_reports_kind(mcp_env):
    mcp_env.state["effects"] = [TimeoutError("slow")]
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings(retries=0))
    assert out["ok"] is False
    assert out["error_kind"] == "timeout"


def test_protocol_error_not_retried_reports_kind(mcp_env):
    mcp_env.state["effects"] = [
        mcp_client._MCPError("protocol", "json-rpc: method not found")
    ]
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is False
    assert out["error_kind"] == "protocol"
    assert "method not found" in out["error"]
    assert mcp_env.calls["n"] == 1  # 协议错误不重试


def test_connection_error_classified_as_process_crash(mcp_env):
    mcp_env.state["effects"] = [ConnectionError("broken pipe")]
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings(retries=0))
    assert out["ok"] is False
    assert out["error_kind"] == "process_crash"


def test_stderr_secret_redacted_in_error(mcp_env):
    secret = "sk-abcDEF1234567890"
    mcp_env.state["effects"] = [RuntimeError(f"server refused: api_key={secret}")]
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is False
    assert secret not in out["error"]
    assert "[REDACTED]" in out["error"]


def test_password_style_secret_redacted(mcp_env):
    mcp_env.state["effects"] = [RuntimeError("auth failed, password: hunter2")]
    out = mcp_client._call_tool("s1", "search", {}, settings=_settings())
    assert out["ok"] is False
    assert "hunter2" not in out["error"]
    assert "[REDACTED]" in out["error"]


def test_stdout_eof_is_crash_even_when_poll_lags():
    """根因回归：进程死后存在窗口 —— stdout 已 EOF 但 poll() 仍为 None
    （waitpid 调度延迟，实测可达数十 ms）。EOF 本身即判定 crash，不误判 timeout。"""
    from types import SimpleNamespace as NS

    sess = mcp_client._StdioSession("true", [])
    sess._proc = NS(
        pid=99999,
        stdin=NS(write=lambda s: None, flush=lambda: None),
        stdout=NS(readline=lambda: ""),  # EOF
        stderr=None,
        poll=lambda: None,  # 内核延迟：尚未可 wait
        returncode=None,
    )
    with pytest.raises(mcp_client._MCPProcessCrash) as ei:
        sess._request("tools/call", {}, timeout_s=5.0)
    assert ei.value.kind == "process_crash"


def test_probe_failure_reports_kind(mcp_env):
    mcp_env.state["effects"] = [TimeoutError("slow")]
    server = {
        "id": "s1",
        "command": "true",
        "args": [],
        "enabled": True,
        "env": {},
        "transport": "stdio",
    }
    out = mcp_client.probe_server(server, settings=_settings(retries=0))
    assert out["ok"] is False
    assert out["error_kind"] == "timeout"
