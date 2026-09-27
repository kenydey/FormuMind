"""Session-level MCP writeish tool grants (not global permanent)."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
# In-memory: session_id -> {server_id: {tool_name: expires_at|None}}
_GRANTS: dict[str, dict[str, dict[str, float | None]]] = {}


def _persist_path() -> Path:
    return Path("./data").resolve() / "mcp_session_grants.json"


def _load_disk() -> None:
    path = _persist_path()
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            with _LOCK:
                _GRANTS.update(raw)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        logger.debug("mcp grants load skipped: %s", exc)


def _save_disk() -> None:
    path = _persist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        path.write_text(json.dumps(_GRANTS, ensure_ascii=False, indent=2), encoding="utf-8")


_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        _load_disk()
        _loaded = True


def approve_session(
    session_id: str,
    server_id: str,
    *,
    tool_name: str | None = None,
    until_session_end: bool = True,
) -> dict[str, Any]:
    """Grant writeish tool(s) for this chat session."""
    _ensure_loaded()
    sid = (session_id or "").strip()
    srv = (server_id or "").strip()
    if not sid or not srv:
        raise ValueError("session_id and server_id required")
    tool = (tool_name or "*").strip() or "*"
    expires: float | None = None if until_session_end else time.time() + 3600
    with _LOCK:
        bucket = _GRANTS.setdefault(sid, {})
        tools = bucket.setdefault(srv, {})
        tools[tool] = expires
    _save_disk()
    return {
        "ok": True,
        "session_id": sid,
        "server_id": srv,
        "tool_name": tool,
        "until_session_end": until_session_end,
    }


def is_granted(session_id: str, server_id: str, tool_name: str) -> bool:
    _ensure_loaded()
    sid = (session_id or "").strip()
    srv = (server_id or "").strip()
    tool = (tool_name or "").strip()
    if not sid or not srv or not tool:
        return False
    with _LOCK:
        tools = (_GRANTS.get(sid) or {}).get(srv) or {}
        for key in (tool, "*"):
            if key not in tools:
                continue
            exp = tools[key]
            if exp is None or exp > time.time():
                return True
    return False


def revoke_session(session_id: str) -> None:
    _ensure_loaded()
    with _LOCK:
        _GRANTS.pop((session_id or "").strip(), None)
    _save_disk()


def list_grants(session_id: str | None = None) -> dict[str, Any]:
    _ensure_loaded()
    with _LOCK:
        if session_id:
            return dict(_GRANTS.get(session_id) or {})
        return {k: dict(v) for k, v in _GRANTS.items()}
