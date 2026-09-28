"""Tests for W1-11: MCP retry (timeout/connection only, exponential backoff)."""
from __future__ import annotations

import json
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import mcp_client


@pytest.fixture()
def fake_session(monkeypatch: pytest.MonkeyPatch):
    """替身 _StdioSession：start/close 为空操作，_request 按 side_effect 行为。

    返回 (state, sleeps)：state["calls"] 为 _request 调用次数，
    sleeps 记录 _sleep 的延迟序列。调用 fake_session.plan([...]) 设置行为。
    """
    # A5 回归：_reset_client_cache 清除 _MCP_SHUTDOWN 等模块级状态，防止
    # 全量套件中先行的 lifespan shutdown 毒化后续测试。
    mcp_client._reset_client_cache()
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


# ---------------------------------------------------------------------------
# B-5 回归（2026-09-28）：_request timeout 真实生效 + 并发不饿死
# ---------------------------------------------------------------------------


class _FakeStdin:
    """线程安全的假 stdin：记录写入的 JSON-RPC payload。"""

    def __init__(self) -> None:
        self.writes: list[str] = []
        self._lock = threading.Lock()

    def write(self, data: str) -> None:
        with self._lock:
            self.writes.append(data)

    def flush(self) -> None:
        pass

    def request_ids(self) -> list:
        with self._lock:
            return [json.loads(w)["id"] for w in self.writes]


class _NeverRespondStdout:
    """永久阻塞的 stdout：readline 永不返回（模拟子进程存活但无输出换行）。"""

    def readline(self) -> str:
        threading.Event().wait(3600)
        return ""


class _FakePopen:
    def __init__(self, stdin: _FakeStdin, stdout: Any) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = None
        self._terminated = False

    def poll(self) -> int | None:
        return 0 if self._terminated else None

    def terminate(self) -> None:
        self._terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self._terminated = True


def _patch_popen(monkeypatch: pytest.MonkeyPatch, stdin: _FakeStdin, stdout: Any) -> None:
    monkeypatch.setattr(
        mcp_client.subprocess,
        "Popen",
        lambda *a, **k: _FakePopen(stdin, stdout),
    )


def test_b5_request_timeout_does_not_hang(monkeypatch: pytest.MonkeyPatch):
    """B-5：子进程不输出换行时，timeout 内抛 TimeoutError 而不是永久阻塞。

    旧实现会在 readline() 里永久卡住（deadline 检查执行不到）；回归目标是
    timeout_s=0.5 时 5 秒内必返回。
    """
    stdin, stdout = _FakeStdin(), _NeverRespondStdout()
    _patch_popen(monkeypatch, stdin, stdout)
    sess = mcp_client._StdioSession("fake-server", [])
    t0 = time.monotonic()
    with pytest.raises(TimeoutError, match="MCP timeout"):
        sess.start(timeout_s=0.5)
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"timeout 未生效，阻塞了 {elapsed:.1f}s"


class _SecondRequestOnlyStdout:
    """模拟 server：只回复第 2 个请求，第 1 个永远不回复。"""

    def __init__(self, stdin: _FakeStdin) -> None:
        self._stdin = stdin
        self._responded = False

    def readline(self) -> str:
        while True:
            ids = self._stdin.request_ids()
            if len(ids) >= 2 and not self._responded:
                self._responded = True
                rid = ids[1]
                return (
                    json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"echo": rid}})
                    + "\n"
                )
            time.sleep(0.01)


def test_b5_concurrent_requests_no_starvation(monkeypatch: pytest.MonkeyPatch):
    """B-5：一个请求卡住（1s 超时）时，另一个请求仍能正常收发。

    旧实现全程持有 session 锁，第二个请求会被饿死；新实现锁只覆盖写+id 分配。
    """
    stdin = _FakeStdin()
    stdout = _SecondRequestOnlyStdout(stdin)
    _patch_popen(monkeypatch, stdin, stdout)
    sess = mcp_client._StdioSession("fake-server", [])
    # 绕开 initialize 握手：直接装配 proc + reader，专测 _request 并发语义
    sess._proc = _FakePopen(stdin, stdout)
    sess._start_reader()

    outcomes: dict[str, Any] = {}
    order: list[str] = []

    def call_first() -> None:
        try:
            outcomes["first"] = sess._request("m1", {}, timeout_s=1.0)
        except Exception as exc:  # noqa: BLE001
            outcomes["first"] = exc
        finally:
            order.append("first")

    def call_second() -> None:
        try:
            outcomes["second"] = sess._request("m2", {}, timeout_s=5.0)
        except Exception as exc:  # noqa: BLE001
            outcomes["second"] = exc
        finally:
            order.append("second")

    t1 = threading.Thread(target=call_first)
    t1.start()
    # 等第 1 个请求的写入落地，保证 id 分配顺序（first=id 1, second=id 2）
    deadline = time.monotonic() + 5
    while len(stdin.request_ids()) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    t2 = threading.Thread(target=call_second)
    t2.start()
    t1.join(10)
    t2.join(10)

    assert isinstance(outcomes["first"], TimeoutError)
    assert outcomes["second"] == {"echo": 2}
    assert order[0] == "second", "第 2 个请求被第 1 个饿死"


# ---------------------------------------------------------------------------
# B-12 回归（2026-09-28）：shutdown_all_sessions 回收子进程
# ---------------------------------------------------------------------------

_ECHO_SERVER = (
    "import sys, json\n"
    "for line in sys.stdin:\n"
    "    try:\n"
    "        req = json.loads(line)\n"
    "    except Exception:\n"
    "        continue\n"
    "    rid = req.get('id')\n"
    "    if rid is None:\n"
    "        continue\n"
    "    m = req.get('method')\n"
    "    res = {'tools': []} if m == 'tools/list' else {'ok': True}\n"
    "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': res}) + chr(10))\n"
    "    sys.stdout.flush()\n"
)


def test_b12_shutdown_all_sessions_reaps_process():
    """B-12：shutdown 后子进程被回收、缓存清空、幂等、拒绝重建。"""
    mcp_client._reset_client_cache()
    try:
        sess = mcp_client._StdioSession(sys.executable, ["-c", _ECHO_SERVER])
        sess.start(timeout_s=10)
        proc = sess._proc
        assert proc is not None and proc.poll() is None
        # 模拟生产状态：session 已进入 client 缓存
        mcp_client._CLIENT_CACHE["s-shutdown-test"] = {
            "digest": "d",
            "generation": 0,
            "session": sess,
        }
        mcp_client.shutdown_all_sessions()
        assert proc.poll() is not None, "子进程未被回收"
        assert mcp_client._CLIENT_CACHE == {}
        # 幂等：再次调用不抛错
        mcp_client.shutdown_all_sessions()
        # shutdown 后拒绝重建（防止迟到创建者复活 session）
        with pytest.raises(mcp_client._MCPError, match="shut down"):
            mcp_client._acquire_session(
                {"id": "s-shutdown-test", "command": "true", "args": []}
            )
    finally:
        mcp_client._reset_client_cache()
