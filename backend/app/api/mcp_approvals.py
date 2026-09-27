"""MCP approval REST API (W3-11 / P1-3 UI).

Backs the frontend McpApprovalDialog: list pending approval requests and
submit allow/deny decisions. The service layer (services/mcp_approval) holds
all business logic; this module is a thin HTTP adapter. Auth is enforced by
the global bearer-token middleware, same as the other routers.
"""
from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import mcp_approval

router = APIRouter(prefix="/api/mcp/approvals", tags=["mcp-approvals"])


class DecideRequest(BaseModel):
    decision: str = Field(pattern="^(allow|deny)$")
    scope: str = Field(default="once", pattern="^(once|session|project|global)$")


@router.get("/pending", response_model=dict)
def list_pending() -> dict:
    """Return currently pending MCP tool approval requests."""
    db = mcp_approval.ensure_store()
    now = time.monotonic()
    items: list[dict] = []
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, server_id, tool_name, session_id, project_id, requested_at "
            "FROM mcp_approval_requests WHERE status = 'pending' "
            "ORDER BY requested_at ASC"
        ).fetchall()
    for r in rows:
        age = now - float(r["requested_at"])
        if age >= mcp_approval.ASK_TIMEOUT_S:
            continue  # will be expired by the next evaluate_call; not shown as actionable
        items.append(
            {
                "request_id": r["id"],
                "server_id": r["server_id"],
                "tool_name": r["tool_name"],
                "session_id": r["session_id"],
                "project_id": r["project_id"],
                "age_s": round(age, 1),
            }
        )
    return {"items": items, "total": len(items)}


@router.post("/{request_id}/decide", response_model=dict)
def decide(request_id: int, req: DecideRequest) -> dict:
    """Submit an allow/deny decision for a pending approval request."""
    try:
        result = mcp_approval.decide_approval(
            request_id, req.decision, scope=req.scope
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "unknown"))
    return result
