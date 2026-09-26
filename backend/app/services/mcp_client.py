"""Minimal MCP stdio client (Phase 4b) — list/call tools, read-only bias.

Protocol subset: initialize → tools/list → tools/call over newline JSON-RPC.
Disabled unless ``mcp_client_enabled``. Fail-open on errors.
"""
from __future__ import annotations

import json
import logging
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


class _StdioSession:
    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None):
        self.command = command
        self.args = args
        self.env = env or {}
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._lock = threading.Lock()

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

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._proc = None

    def _notify(self, method: str, params: dict) -> None:
        if not self._proc or not self._proc.stdin:
            return
        line = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: dict, *, timeout_s: float = 8.0) -> Any:
        if not self._proc or not self._proc.stdin or not self._proc.stdout:
            raise RuntimeError("MCP process not started")
        with self._lock:
            self._id += 1
            req_id = self._id
            payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                line = self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") != req_id:
                    continue
                if "error" in msg:
                    raise RuntimeError(str(msg["error"])[:300])
                return msg.get("result")
            raise TimeoutError(f"MCP timeout on {method}")


def probe_server(server: dict[str, Any], *, timeout_s: float = 8.0) -> dict[str, Any]:
    if (server.get("transport") or "stdio") != "stdio":
        return {"ok": False, "error": "only stdio transport supported in MVP", "tools": []}
    sess = _StdioSession(server["command"], list(server.get("args") or []), server.get("env"))
    try:
        sess.start(timeout_s=timeout_s)
        result = sess._request("tools/list", {}, timeout_s=timeout_s) or {}
        tools = result.get("tools") if isinstance(result, dict) else []
        names = [t.get("name") for t in (tools or []) if isinstance(t, dict)]
        return {"ok": True, "tools": names, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "tools": [], "error": str(exc)[:300]}
    finally:
        sess.close()


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
        return {"ok": False, "error": f"write-like tool denied: {tool_name}"}
    server = next((s for s in _prefs_servers() if s.get("id") == server_id and s.get("enabled")), None)
    if not server:
        return {"ok": False, "error": "server not found or disabled"}
    sess = _StdioSession(server["command"], list(server.get("args") or []), server.get("env"))
    try:
        sess.start()
        result = sess._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
            timeout_s=20.0,
        )
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}
    finally:
        sess.close()
