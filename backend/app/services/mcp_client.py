"""Minimal MCP stdio client (Phase 4b) — list/call tools, read-only bias.

Protocol subset: initialize → tools/list → tools/call over newline JSON-RPC.
Disabled unless ``mcp_client_enabled``. Fail-open on errors.

W5-5: 单 server 单连接缓存（_acquire_session）+ 配置 generation 屏障 +
并发创建去重；失败分类上报（error_kind）；日志/错误上报前 stderr 脱敏。
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_WRITEISH = frozenset(
    {
        "write",
        "delete",
        "remove",
        "unlink",
        "update",
        "create",
        "put",
        "post",
        "exec",
        "shell",
        "run",
    }
)


# ---------------------------------------------------------------------------
# W5-5: secret redaction（日志/错误上报前过滤疑似密钥）
# 模式复刻 agent_memory._SECRET_PATTERNS（该文件注明 kept local on purpose）
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"sk-[A-Za-z0-9]{8,}",  # OpenAI-style keys
        r"AKIA[0-9A-Z]{16}",  # AWS access key id
        r"github_pat_[A-Za-z0-9_]+",
        r"gh[pousr]_[A-Za-z0-9]+",
        r"xox[bap]-",
        r"password\s*[:=]\s*\S+",
        r"passwd\s*[:=]\s*\S+",
        r"api[_-]?key\s*[:=]\s*\S+",
        r"secret\s*[:=]\s*\S+",
        r"token\s*[:=]\s*\S+",
    )
)

_REDACTED = "[REDACTED]"


def _redact_secrets(text: str | None) -> str:
    """替换疑似密钥模式；先脱敏后截断，避免密钥横跨截断边界。"""
    redacted = str(text or "")
    for pat in _SECRET_PATTERNS:
        redacted = pat.sub(_REDACTED, redacted)
    return redacted


# ---------------------------------------------------------------------------
# W5-5: 失败分类
# ---------------------------------------------------------------------------


class _MCPError(Exception):
    """带分类的 MCP 错误。kind ∈ {timeout, protocol, process_crash, unknown}。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class _MCPProcessCrash(_MCPError):
    """子进程崩溃：可重试（_call_once 会先逐出旧连接，重试时重建）。"""

    def __init__(self, message: str) -> None:
        super().__init__("process_crash", message)


# 可重试异常：超时/连接 + 进程崩溃
_RETRYABLE: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    _MCPProcessCrash,
)


def _classify_error(exc: BaseException) -> str:
    """失败分类：timeout / protocol / process_crash / unknown。"""
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, _MCPError):
        return exc.kind
    if isinstance(exc, (ConnectionError, OSError)):
        # broken pipe / 读写失败多为进程已死
        return "process_crash"
    return "unknown"


def _prefs_servers() -> list[dict[str, Any]]:
    from .skills_store import load_prefs

    return list(load_prefs().get("mcp_servers") or [])


def list_mcp_servers() -> list[dict[str, Any]]:
    return _prefs_servers()


def save_mcp_servers(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .skills_store import save_prefs

    cleaned: list[dict[str, Any]] = []
    for s in servers:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        cmd = s.get("command")
        if not sid or not cmd:
            continue
        cleaned.append(
            {
                "id": sid,
                "command": str(cmd),
                "args": list(s.get("args") or []),
                "enabled": bool(s.get("enabled", True)),
                "env": dict(s.get("env") or {}),
                "transport": str(s.get("transport") or "stdio"),
            }
        )
    save_prefs({"mcp_servers": cleaned})
    return cleaned


def _is_writeish(name: str) -> bool:
    n = (name or "").lower()
    return any(tok in n for tok in _WRITEISH)


def _sleep(seconds: float) -> None:
    """sleep 的薄封装，便于测试替换。"""
    time.sleep(seconds)


def _retry_params(settings: Any = None) -> tuple[int, float]:
    """读取重试配置；settings 缺失或读取失败时用保守默认值。"""
    candidates: list[Any] = []
    if settings is not None:
        candidates.append(settings)
    else:
        try:
            from ..config import get_settings

            candidates.append(get_settings())
        except Exception:  # noqa: BLE001
            pass
    for s in candidates:
        try:
            retries = getattr(s, "mcp_tool_retries", 2)
            backoff_s = getattr(s, "mcp_retry_backoff_s", 0.5)
            return (
                max(0, int(retries) if retries is not None else 2),
                max(0.0, float(backoff_s) if backoff_s is not None else 0.5),
            )
        except (TypeError, ValueError):  # noqa: BLE001
            continue
    return 2, 0.5


def _with_retry(
    fn,
    *,
    retries: int = 2,
    backoff_s: float = 0.5,
    retry_on: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError),
):
    """带指数退避的重试：仅 retry_on 类错误（超时/连接）重试。

    退避序列为 backoff_s * 3**attempt（默认 0.5s → 1.5s）。
    业务错误（如 JSON-RPC error → RuntimeError）直接抛出，不重试。
    重试耗尽后抛出最后一次异常，由调用方转为 fail-open 的 error 返回。
    """
    attempt = 0
    while True:
        try:
            return fn()
        except retry_on as exc:
            if attempt >= retries:
                raise
            delay = backoff_s * (3**attempt)
            logger.warning(
                "MCP 调用失败，%.1fs 后第 %d/%d 次重试：%s",
                delay,
                attempt + 1,
                retries,
                _redact_secrets(str(exc))[:300],
            )
            _sleep(delay)
            attempt += 1


# reader 线程 → 等待队列的"连接死亡"哨兵（stdout EOF/读异常时投递）。
_EOF: Any = object()


class _StdioSession:
    # B-5 修复（2026-09-28）：_request 的 timeout 原先形同虚设——阻塞式
    # stdout.readline() 在子进程不输出换行时永久阻塞，且全程持有 _lock，
    # 同一 session 的其他请求一并饿死。现改为：
    #   - 独立 daemon reader 线程阻塞读 stdout，按响应 id 路由到各请求的队列；
    #   - _request 只在队列上按剩余时间 queue.get(timeout=...)，锁只保护
    #     写操作 + id 分配 + pending 注册，不覆盖等待；
    #   - 超时后从 pending 摘除该请求（迟到响应被 reader 丢弃），调用方
    #     不再永久阻塞；stdout EOF 时 reader 唤醒所有等待者报连接死亡。

    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None):
        self.command = command
        self.args = args
        self.env = env or {}
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._lock = threading.Lock()
        self._started = False
        self._pending: dict[int, queue.SimpleQueue] = {}
        self._reader: threading.Thread | None = None

    def start(self, timeout_s: float = 8.0) -> None:
        import os

        env = os.environ.copy()
        env.update({k: str(v) for k, v in self.env.items()})
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )
        self._start_reader()
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "formumind", "version": "1.0"},
            },
            timeout_s=timeout_s,
        )
        # notifications/initialized (no id)
        self._notify("notifications/initialized", {})
        self._started = True

    def _start_reader(self) -> None:
        """启动 stdout reader 线程（幂等：已有存活 reader 则不重复启动）。"""
        if self._reader is not None and self._reader.is_alive():
            return
        t = threading.Thread(
            target=self._reader_loop,
            name=f"mcp-reader-{self.command}",
            daemon=True,
        )
        self._reader = t
        t.start()

    def _reader_loop(self) -> None:
        """阻塞读 stdout 行，按 id 路由到各请求的等待队列。

        EOF/异常时用 _EOF 哨兵唤醒所有等待者（按连接死亡处理），
        迟到（已超时摘除）的响应直接丢弃。
        """
        proc = self._proc
        stdout = proc.stdout if proc is not None else None
        try:
            while True:
                line = stdout.readline() if stdout is not None else ""
                if not line:
                    break  # EOF：server 退出/管道断开
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 非 JSON 行（如 server 日志混入 stdout）跳过
                msg_id = msg.get("id")
                with self._lock:
                    target = self._pending.get(msg_id)
                if target is not None:
                    target.put(msg)
                # 无匹配（无 id / 请求已超时摘除）→ 丢弃，不阻塞他人
        except Exception:  # noqa: BLE001
            logger.debug("MCP reader loop 异常退出", exc_info=True)
        finally:
            with self._lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for q in pending:
                q.put(_EOF)

    def alive(self) -> bool:
        """连接是否可用：已成功启动、未关闭、进程未退出。"""
        if not self._started:
            return False
        proc = self._proc
        return proc is None or proc.poll() is None

    def close(self) -> None:
        proc, reader = self._proc, self._reader
        self._proc = None
        self._reader = None
        self._started = False
        # 先唤醒所有等待中的请求（按连接死亡处理），再回收进程/线程
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for q in pending:
            q.put(_EOF)
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except Exception:  # noqa: BLE001
                        proc.kill()
            except Exception:  # noqa: BLE001
                pass
        if (
            reader is not None
            and reader.is_alive()
            and reader is not threading.current_thread()
        ):
            reader.join(timeout=2)

    def _stderr_tail(self, limit: int = 500) -> str:
        """进程已死时尽力读取残留 stderr（脱敏后返回）。

        用 select 限时等待，避免卡在"stdout 已 EOF 但 stderr 写端尚未关闭"
        的极窄窗口上。
        """
        try:
            proc = self._proc
            if proc is None or proc.stderr is None:
                return ""
            import select

            readable, _, _ = select.select([proc.stderr], [], [], 0.5)
            if not readable:
                return ""
            data = proc.stderr.read() or ""
        except Exception:  # noqa: BLE001
            return ""
        tail = _redact_secrets(data[-limit:]).strip()
        return f"; stderr: {tail}" if tail else ""

    def _notify(self, method: str, params: dict) -> None:
        if not self._proc or not self._proc.stdin:
            return
        line = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: dict, *, timeout_s: float = 8.0) -> Any:
        if not self._proc or not self._proc.stdin or not self._proc.stdout:
            raise _MCPError("protocol", "MCP process not started")
        reader = self._reader
        if reader is None or not reader.is_alive():
            raise _MCPProcessCrash(f"MCP reader dead on {method}")
        # 锁只保护：id 分配 + pending 注册 + stdin 写（防交错），不覆盖等待
        with self._lock:
            self._id += 1
            req_id = self._id
            waiter: queue.SimpleQueue = queue.SimpleQueue()
            self._pending[req_id] = waiter
            payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            try:
                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self._pending.pop(req_id, None)
                raise _MCPProcessCrash(
                    f"MCP write failed on {method}: {exc}"
                ) from exc
        deadline = time.time() + timeout_s
        try:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"MCP timeout on {method}")
                try:
                    msg = waiter.get(timeout=remaining)
                except queue.Empty:
                    raise TimeoutError(f"MCP timeout on {method}")
                if msg is _EOF:
                    # stdout 已 EOF：server 正在退出/已退出。注意 poll() 在此
                    # 时可能因内核调度延迟仍返回 None（实测可达数十 ms），不能
                    # 以它为准 —— EOF 本身就是连接死亡的权威证据。
                    proc = self._proc
                    code = proc.poll() if proc is not None else None
                    raise _MCPProcessCrash(
                        f"MCP connection lost (stdout EOF) on {method}"
                        + (f" (exit code={code})" if code is not None else "")
                        + self._stderr_tail()
                    )
                if "error" in msg:
                    raise _MCPError(
                        "protocol",
                        f"json-rpc error on {method}: {msg['error']}"[:300],
                    )
                return msg.get("result")
        finally:
            # 超时/异常/正常返回后摘除：迟到响应会被 reader 丢弃，不再阻塞调用方
            with self._lock:
                self._pending.pop(req_id, None)


# ---------------------------------------------------------------------------
# W5-5: 连接缓存（单 server 单 session）+ generation 屏障 + 并发去重
# ---------------------------------------------------------------------------

_CLIENT_CACHE: dict[str, dict[str, Any]] = {}
_CLIENT_LOCK = threading.Lock()
_CONFIG_GENERATIONS: dict[str, int] = {}
_CREATING: Any = object()  # 占位：已有线程正在创建该 server 的连接
# B-12：shutdown 标记。shutdown_all_sessions() 置位后 _acquire_session 拒绝
# 重建连接（防止关闭后迟到的创建者复活 session）；_reset_client_cache 清零
# （测试隔离用）。
_MCP_SHUTDOWN = False


def shutdown_all_sessions() -> None:
    """B-12：关闭全部缓存的 MCP session（lifespan shutdown 调用）。

    关闭 client 缓存、terminate 子进程、join reader 线程。
    幂等；单个 session 关闭失败不阻断其余（异常安全）。
    """
    global _MCP_SHUTDOWN
    with _CLIENT_LOCK:
        entries = [e for e in _CLIENT_CACHE.values() if e is not _CREATING]
        _CLIENT_CACHE.clear()
        _CONFIG_GENERATIONS.clear()
        _MCP_SHUTDOWN = True
    for entry in entries:
        try:
            sess = entry.get("session")
            if sess is not None:
                sess.close()
        except Exception:  # noqa: BLE001
            logger.exception("MCP shutdown: 关闭 session 失败（已忽略，继续其余）")


def _server_config_digest(server: dict[str, Any]) -> str:
    """server 配置摘要：command/args/env/transport 任一变化即视为新配置。"""
    canonical = json.dumps(
        {
            "command": server.get("command"),
            "args": list(server.get("args") or []),
            "env": dict(server.get("env") or {}),
            "transport": server.get("transport") or "stdio",
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _close_quietly(sess: _StdioSession | None) -> None:
    if sess is None:
        return
    try:
        sess.close()
    except Exception:  # noqa: BLE001
        pass


def _reset_client_cache() -> None:
    """清空连接缓存并重置 generation（测试隔离用；生产不调用）。

    同时清除 shutdown 标记，保证测试间隔离。
    """
    global _MCP_SHUTDOWN
    with _CLIENT_LOCK:
        for entry in _CLIENT_CACHE.values():
            if entry is not _CREATING:
                _close_quietly(entry.get("session"))
        _CLIENT_CACHE.clear()
        _CONFIG_GENERATIONS.clear()
        _MCP_SHUTDOWN = False


def _cache_info() -> dict[str, dict[str, Any]]:
    """缓存自检：{server_id: {generation, digest, alive}}。"""
    with _CLIENT_LOCK:
        return {
            sid: {
                "generation": entry["generation"],
                "digest": entry["digest"],
                "alive": entry["session"].alive(),
            }
            for sid, entry in _CLIENT_CACHE.items()
            if entry is not _CREATING
        }


def _invalidate_session(server_id: str) -> None:
    """逐出缓存（崩溃/失效时调用；下次调用重建）。generation 不变。"""
    to_close: _StdioSession | None = None
    with _CLIENT_LOCK:
        entry = _CLIENT_CACHE.pop(server_id, None)
        if entry is not None and entry is not _CREATING:
            to_close = entry.get("session")
    _close_quietly(to_close)


def _acquire_session(server: dict[str, Any], *, timeout_s: float = 8.0) -> _StdioSession:
    """获取 server 的缓存 session：命中且 alive 直接复用，否则单例创建。

    - 配置 digest 变化 → 旧 session 失效：generation 递增（屏障），关闭旧连接后重建。
    - 已死连接同样失效重建。
    - 并发创建去重：首个线程创建，其余等待后复用同一在途连接。
    """
    server_id = str(server.get("id") or "")
    if not server_id:
        raise _MCPError("protocol", "server id missing")
    if _MCP_SHUTDOWN:
        # B-12：shutdown 后拒绝重建，防止迟到创建者复活 session
        raise _MCPError("protocol", "MCP client shut down")
    digest = _server_config_digest(server)
    to_close: _StdioSession | None = None
    wait = False
    with _CLIENT_LOCK:
        entry = _CLIENT_CACHE.get(server_id)
        if entry is not None and entry is not _CREATING:
            sess = entry["session"]
            if entry.get("digest") == digest and sess.alive():
                return sess
            # 配置变更或连接已死 → generation 屏障：旧代失效
            _CONFIG_GENERATIONS[server_id] = _CONFIG_GENERATIONS.get(server_id, 0) + 1
            to_close = sess
            del _CLIENT_CACHE[server_id]
            entry = None
        if entry is _CREATING:
            wait = True  # 别的线程正在创建 → 等待复用
        else:
            _CLIENT_CACHE[server_id] = _CREATING
    _close_quietly(to_close)

    if wait:
        # 并发去重：等待创建者完成（时限 timeout_s + 5s，超时则自己接手）
        deadline = time.time() + timeout_s + 5.0
        while time.time() < deadline:
            _sleep(0.05)
            with _CLIENT_LOCK:
                entry = _CLIENT_CACHE.get(server_id)
            if entry is None:
                break  # 创建者失败已清理 → 自己接手
            if entry is _CREATING:
                continue
            sess = entry["session"]
            if entry.get("digest") == digest and sess.alive():
                return sess
            break  # 期间失效 → 自己接手
        return _acquire_session(server, timeout_s=timeout_s)

    # 创建者路径：start 可能阻塞（≤ timeout_s），全程不持全局锁
    generation = _CONFIG_GENERATIONS.get(server_id, 0)
    sess = _StdioSession(
        server["command"], list(server.get("args") or []), server.get("env")
    )
    try:
        sess.start(timeout_s=timeout_s)
    except Exception:
        with _CLIENT_LOCK:
            if _CLIENT_CACHE.get(server_id) is _CREATING:
                del _CLIENT_CACHE[server_id]
        raise
    with _CLIENT_LOCK:
        if (
            _CLIENT_CACHE.get(server_id) is _CREATING
            and _server_config_digest(server) == digest
        ):
            _CLIENT_CACHE[server_id] = {
                "digest": digest,
                "generation": generation,
                "session": sess,
            }
            return sess
    # 创建期间配置又变了 → 丢弃本次，按新配置重来（generation 屏障）
    _close_quietly(sess)
    return _acquire_session(server, timeout_s=timeout_s)


def probe_server(
    server: dict[str, Any],
    *,
    timeout_s: float = 8.0,
    settings: Any = None,
) -> dict[str, Any]:
    """探测 MCP server，返回完整工具描述符。

    返回 {"ok", "tools", "names", "error"}；tools 为
    [{"name", "description", "inputSchema"}]，names 为纯名称列表（兼容旧调用方）。
    仅超时/连接类错误走指数退避重试（_with_retry），业务错误直接返回 error。
    """
    if (server.get("transport") or "stdio") != "stdio":
        return {
            "ok": False,
            "error": "only stdio transport supported in MVP",
            "tools": [],
            "names": [],
        }
    retries, backoff_s = _retry_params(settings)

    def _probe_once() -> dict[str, Any]:
        sess = _acquire_session(server, timeout_s=timeout_s)
        try:
            result = sess._request("tools/list", {}, timeout_s=timeout_s) or {}
        except Exception as exc:  # noqa: BLE001
            # 进程崩溃 → 逐出缓存，重试时重建新连接
            if _classify_error(exc) == "process_crash":
                _invalidate_session(str(server.get("id") or ""))
            raise
        raw = result.get("tools") if isinstance(result, dict) else []
        descriptors: list[dict[str, Any]] = []
        names: list[str] = []
        for t in raw or []:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            name = str(t["name"])
            names.append(name)
            schema = t.get("inputSchema")
            descriptors.append(
                {
                    "name": name,
                    "description": str(t.get("description") or ""),
                    "inputSchema": schema if isinstance(schema, dict) else {},
                }
            )
        return {"ok": True, "tools": descriptors, "names": names, "error": None}

    try:
        return _with_retry(
            _probe_once, retries=retries, backoff_s=backoff_s, retry_on=_RETRYABLE
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "tools": [],
            "names": [],
            "error": _redact_secrets(str(exc))[:300],
            "error_kind": _classify_error(exc),
        }


def is_writeish_tool(tool_name: str) -> bool:
    return _is_writeish(tool_name)


def _approval_gate(
    server_id: str,
    tool_name: str,
    *,
    settings: Any = None,
) -> dict[str, Any] | None:
    """W2-7 MCP 逐工具审批门禁。返回 None 表示放行，否则返回 error dict。

    flag ``mcp_approval_enabled`` 默认关；审批模块异常时按 ask(deny) 处理，
    绝不让工具在审批崩溃时静默放行。
    """
    enabled = False
    try:
        s = settings
        if s is None:
            from ..config import get_settings

            s = get_settings()
        enabled = bool(getattr(s, "mcp_approval_enabled", False))
    except Exception:  # noqa: BLE001
        enabled = False
    if not enabled:
        return None
    try:
        from .mcp_approval import evaluate_call

        verdict = evaluate_call(server_id, tool_name)
    except Exception:  # noqa: BLE001
        logger.exception("MCP 审批模块异常，按 ask(deny) 处理：%s/%s", server_id, tool_name)
        return {
            "ok": False,
            "error": "mcp_approval_unavailable",
            "mcp_approval_required": {
                "server_id": server_id,
                "tool_name": tool_name,
            },
        }
    if verdict.get("verdict") == "allow":
        return None
    payload: dict[str, Any] = {
        "ok": False,
        "error": str(verdict.get("reason") or "mcp_approval_denied"),
        "mcp_approval_required": {
            "server_id": server_id,
            "tool_name": tool_name,
        },
    }
    if verdict.get("request_id") is not None:
        payload["mcp_approval_required"]["request_id"] = verdict["request_id"]
    return payload


def call_tool_readonly(
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    settings: Any = None,
) -> dict[str, Any]:
    if settings is not None and not bool(getattr(settings, "mcp_client_enabled", False)):
        return {"ok": False, "error": "mcp_client_enabled is off"}
    if _is_writeish(tool_name):
        return {
            "ok": False,
            "error": f"write-like tool denied: {tool_name}",
            "mcp_permission_required": {
                "server_id": server_id,
                "tool_name": tool_name,
            },
        }
    return _call_tool(server_id, tool_name, arguments)


def call_tool_with_session(
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    """Call tool; writeish requires prior session grant via approve-session."""
    if settings is not None and not bool(getattr(settings, "mcp_client_enabled", False)):
        return {"ok": False, "error": "mcp_client_enabled is off"}
    if _is_writeish(tool_name):
        from .mcp_session_grants import is_granted

        if not session_id or not is_granted(session_id, server_id, tool_name):
            return {
                "ok": False,
                "error": "mcp_permission_required",
                "mcp_permission_required": {
                    "server_id": server_id,
                    "tool_name": tool_name,
                    "session_id": session_id,
                },
            }
    return _call_tool(server_id, tool_name, arguments)


def _call_tool(
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    settings: Any = None,
) -> dict[str, Any]:
    gate = _approval_gate(server_id, tool_name, settings=settings)
    if gate is not None:
        return gate
    server = next((s for s in _prefs_servers() if s.get("id") == server_id and s.get("enabled")), None)
    if not server:
        return {"ok": False, "error": "server not found or disabled"}
    retries, backoff_s = _retry_params(settings)

    def _call_once() -> Any:
        sess = _acquire_session(server)
        try:
            return sess._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments or {}},
                timeout_s=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            # 进程崩溃 → 逐出缓存，重试时重建新连接
            if _classify_error(exc) == "process_crash":
                _invalidate_session(server_id)
            raise

    try:
        result = _with_retry(
            _call_once, retries=retries, backoff_s=backoff_s, retry_on=_RETRYABLE
        )
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": _redact_secrets(str(exc))[:300],
            "error_kind": _classify_error(exc),
        }
