"""Optional MCP tool execution when servers are selected in chat.

MVP: if the user question looks like an explicit tool call
(``/mcp <server> <tool> {...}``) or mentions a single known tool name,
invoke readonly (or session-granted writeish). Otherwise only skill-docs
are injected — no automatic blind tool spam.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_EXPLICIT_RE = re.compile(
    r"(?i)/mcp\s+(\S+)\s+(\S+)(?:\s+(\{.*\}))?\s*$",
)


def maybe_run_selected_mcp(
    question: str,
    server_ids: list[str],
    *,
    session_id: str | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    """Return {results, mcp_permission_required}."""
    from .mcp_client import call_tool_with_session, is_writeish_tool, list_mcp_servers, probe_server

    if not server_ids:
        return {"results": [], "mcp_permission_required": None}
    if settings is not None and not bool(getattr(settings, "mcp_client_enabled", False)):
        return {"results": [], "mcp_permission_required": None}

    enabled = {
        s["id"]: s
        for s in list_mcp_servers()
        if s.get("enabled") and s.get("id") in server_ids
    }
    if not enabled:
        return {"results": [], "mcp_permission_required": None}

    m = _EXPLICIT_RE.search((question or "").strip())
    if not m:
        return {"results": [], "mcp_permission_required": None}

    sid, tool, args_raw = m.group(1), m.group(2), m.group(3)
    if sid not in enabled:
        return {
            "results": [{"ok": False, "error": f"server {sid} not in selected_mcp_servers"}],
            "mcp_permission_required": None,
        }
    args: dict[str, Any] = {}
    if args_raw:
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError:
            args = {}

    # Prefer probing to validate tool exists (soft)
    try:
        probe_server(enabled[sid])
    except Exception:  # noqa: BLE001
        pass

    if is_writeish_tool(tool):
        from .mcp_session_grants import is_granted

        if not session_id or not is_granted(session_id, sid, tool):
            return {
                "results": [],
                "mcp_permission_required": {
                    "server_id": sid,
                    "tool_name": tool,
                    "session_id": session_id,
                    "arguments": args,
                },
            }

    result = call_tool_with_session(
        sid, tool, args, session_id=session_id, settings=settings
    )
    if result.get("mcp_permission_required"):
        return {"results": [], "mcp_permission_required": result["mcp_permission_required"]}
    return {"results": [result], "mcp_permission_required": None}
