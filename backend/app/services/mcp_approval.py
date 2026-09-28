"""MCP 逐工具审批三态（W2-7 / P1-3）。

Allow / Ask / Block 按工具粒度门禁，决策按 once / session / project / global
作用域持久化到独立 SQLite（``mcp_tool_policies``），默认全部 ask。

仓库内无现成的 approval card / 审批机制（2026-09-27 核查结论），因此：
ask 无决策时后端默认 deny 并写审计日志，request_id 留给未来的前端审批 UI；
审批模块自身异常时按 ask(deny) 处理，绝不静默放行。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DECISIONS = ("allow", "ask", "block")
SCOPES = ("once", "session", "project", "global")

# ask 挂起超时：5 分钟无决策自动 deny。用单调时钟，不起常驻线程。
ASK_TIMEOUT_S = 300.0

_LOCK = threading.Lock()


def _default_db_path() -> Path:
    path = Path("./data").resolve() / "mcp_approval.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ensure_store(path: str | Path | None = None) -> Path:
    """建表（独立 ensure，不动 alembic），幂等。返回 db 路径。"""
    db = Path(path) if path else _default_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_tool_policies (
                server_id  TEXT NOT NULL,
                tool_name  TEXT NOT NULL,
                scope      TEXT NOT NULL,
                decision   TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (server_id, tool_name, scope)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_approval_requests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id    TEXT NOT NULL,
                tool_name    TEXT NOT NULL,
                session_id   TEXT,
                project_id   TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                requested_at REAL NOT NULL,
                decided_at   REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcp_approval_req_tool "
            "ON mcp_approval_requests (server_id, tool_name, status)"
        )
    return db


def _norm(server_id: str, tool_name: str) -> tuple[str, str]:
    return str(server_id or "").strip(), str(tool_name or "").strip()


def set_policy(
    server_id: str,
    tool_name: str,
    decision: str,
    scope: str = "global",
    *,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """写入一条策略。decision 非法或 scope 非法时抛 ValueError。"""
    if decision not in DECISIONS:
        raise ValueError(f"decision 必须是 {DECISIONS} 之一，得到 {decision!r}")
    if scope not in SCOPES:
        raise ValueError(f"scope 必须是 {SCOPES} 之一，得到 {scope!r}")
    sid, tool = _norm(server_id, tool_name)
    if not sid or not tool:
        raise ValueError("server_id / tool_name 不能为空")
    db = ensure_store(path)
    now = time.time()
    with _LOCK, sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO mcp_tool_policies "
            "(server_id, tool_name, scope, decision, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (server_id, tool_name, scope) DO UPDATE SET "
            "decision = excluded.decision, updated_at = excluded.updated_at",
            (sid, tool, scope, decision, now, now),
        )
    return {"ok": True, "server_id": sid, "tool_name": tool, "scope": scope, "decision": decision}


def get_policies(
    server_id: str,
    tool_name: str,
    *,
    path: str | Path | None = None,
) -> dict[str, str]:
    """返回 {scope: decision}；无策略时返回 {}（调用方按默认 ask 处理）。"""
    sid, tool = _norm(server_id, tool_name)
    db = ensure_store(path)
    with _LOCK, sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT scope, decision FROM mcp_tool_policies WHERE server_id = ? AND tool_name = ?",
            (sid, tool),
        ).fetchall()
    return {scope: decision for scope, decision in rows}


def effective_decision(policies: dict[str, str]) -> str:
    """多作用域决策合并：block > ask > allow；无策略默认 ask。"""
    vals = set(policies.values())
    if "block" in vals:
        return "block"
    if "ask" in vals:
        return "ask"
    if "allow" in vals:
        return "allow"
    return "ask"


def _pending_request(
    conn: sqlite3.Connection, sid: str, tool: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, requested_at FROM mcp_approval_requests "
        "WHERE server_id = ? AND tool_name = ? AND status = 'pending' "
        "ORDER BY id DESC LIMIT 1",
        (sid, tool),
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "requested_at": row[1]}


def _audit(event: str, sid: str, tool: str, **fields: Any) -> None:
    detail = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.warning("MCP 审批审计 event=%s server=%s tool=%s %s", event, sid, tool, detail)


def evaluate_call(
    server_id: str,
    tool_name: str,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    path: str | Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """审批门禁求值。

    返回 {"verdict": "allow"} 或
    {"verdict": "deny", "reason": ..., "request_id": ...}。
    ``now`` 为单调时钟时间戳（测试注入用），默认 ``time.monotonic()``。
    本函数不抛业务异常；DB 异常向上传播，由调用方按 ask(deny) 处理。
    """
    sid, tool = _norm(server_id, tool_name)
    if not sid or not tool:
        return {"verdict": "deny", "reason": "invalid_tool_identity"}
    mono = time.monotonic() if now is None else now
    db = ensure_store(path)

    eff = effective_decision(get_policies(sid, tool, path=db))
    if eff == "allow":
        return {"verdict": "allow"}
    if eff == "block":
        _audit("policy_block", sid, tool)
        return {"verdict": "deny", "reason": "policy_block"}

    # eff == "ask"：查挂起的审批请求
    with _LOCK, sqlite3.connect(db) as conn:
        pending = _pending_request(conn, sid, tool)
        if pending is None:
            cur = conn.execute(
                "INSERT INTO mcp_approval_requests "
                "(server_id, tool_name, session_id, project_id, status, requested_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (sid, tool, session_id, project_id, mono),
            )
            request_id = cur.lastrowid
            _audit("approval_required", sid, tool, request_id=request_id)
            return {"verdict": "deny", "reason": "approval_required", "request_id": request_id}
        age = mono - float(pending["requested_at"])
        if age >= ASK_TIMEOUT_S:
            conn.execute(
                "UPDATE mcp_approval_requests SET status = 'expired', decided_at = ? WHERE id = ?",
                (mono, pending["id"]),
            )
            _audit("approval_timeout", sid, tool, request_id=pending["id"], age_s=round(age, 1))
            return {"verdict": "deny", "reason": "approval_timeout", "request_id": pending["id"]}
        _audit("approval_pending", sid, tool, request_id=pending["id"], age_s=round(age, 1))
        return {"verdict": "deny", "reason": "approval_pending", "request_id": pending["id"]}


def decide_approval(
    request_id: int,
    decision: str,
    *,
    scope: str = "once",
    path: str | Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """对挂起的审批请求做决策（供未来前端审批 UI 调用）。

    allow → 按 scope 落一条 allow 策略；deny → 仅标记请求，不持久化拒绝。
    """
    if decision not in ("allow", "deny"):
        raise ValueError(f"decision 必须是 allow/deny，得到 {decision!r}")
    if scope not in SCOPES:
        raise ValueError(f"scope 必须是 {SCOPES} 之一，得到 {scope!r}")
    mono = time.monotonic() if now is None else now
    db = ensure_store(path)
    with _LOCK, sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT server_id, tool_name, status FROM mcp_approval_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "request_not_found"}
        sid, tool, status = row
        if status != "pending":
            return {"ok": False, "error": f"request_not_pending: {status}"}
        conn.execute(
            "UPDATE mcp_approval_requests SET status = ?, decided_at = ? WHERE id = ?",
            ("approved" if decision == "allow" else "denied", mono, request_id),
        )
    if decision == "allow":
        set_policy(sid, tool, "allow", scope, path=db)
    _audit("approval_decided", sid, tool, request_id=request_id, decision=decision, scope=scope)
    return {"ok": True, "request_id": request_id, "decision": decision, "scope": scope}
