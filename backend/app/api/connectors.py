"""Builtin connectors + MCP settings API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import connectors_builtin, mcp_client

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


class ConnectorToggle(BaseModel):
    enabled: bool


class McpServerIn(BaseModel):
    id: str
    command: str
    args: list[str] = Field(default_factory=list)
    enabled: bool = True
    env: dict[str, str] = Field(default_factory=dict)
    transport: str = "stdio"


class McpServersReplace(BaseModel):
    servers: list[McpServerIn]


class McpCallRequest(BaseModel):
    server_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.get("")
def list_all() -> dict:
    settings = get_settings()
    return {
        "builtin": connectors_builtin.list_connectors(settings=settings),
        "mcp": mcp_client.list_mcp_servers(),
        "mcp_client_enabled": bool(getattr(settings, "mcp_client_enabled", False)),
        "connectors_builtin_enabled": bool(getattr(settings, "connectors_builtin_enabled", True)),
    }


@router.post("/builtin/{connector_id}/toggle")
def toggle_builtin(connector_id: str, body: ConnectorToggle) -> dict:
    ids = {c["id"] for c in connectors_builtin.CONNECTOR_CATALOG}
    if connector_id not in ids:
        raise HTTPException(status_code=404, detail="unknown connector")
    # set_connector_enabled currently broken — use prefs directly
    from ..services.skills_store import load_prefs, save_prefs

    prefs = load_prefs()
    disabled = set(prefs.get("disabled_connector_ids") or [])
    if body.enabled:
        disabled.discard(connector_id)
    else:
        disabled.add(connector_id)
    save_prefs({"disabled_connector_ids": sorted(disabled)})
    return {"builtin": connectors_builtin.list_connectors(settings=get_settings())}


@router.post("/builtin/{connector_id}/search")
def search_builtin(connector_id: str, q: str = "", limit: int = 8) -> dict:
    settings = get_settings()
    if not bool(getattr(settings, "connectors_builtin_enabled", True)):
        raise HTTPException(status_code=503, detail="builtin connectors disabled")
    if connector_id == "literature":
        rows = connectors_builtin.search_literature(q, limit=limit)
    elif connector_id == "chemistry":
        rows = connectors_builtin.lookup_chemistry(q, limit=limit)
    else:
        raise HTTPException(status_code=404, detail="unknown connector")
    return {"results": [r.model_dump() for r in rows]}


@router.put("/mcp")
def replace_mcp(body: McpServersReplace) -> dict:
    settings = get_settings()
    if not bool(getattr(settings, "mcp_client_enabled", False)):
        raise HTTPException(status_code=503, detail="mcp_client_enabled is off")
    servers = mcp_client.save_mcp_servers([s.model_dump() for s in body.servers])
    return {"mcp": servers}


@router.post("/mcp/{server_id}/probe")
def probe_mcp(server_id: str) -> dict:
    settings = get_settings()
    if not bool(getattr(settings, "mcp_client_enabled", False)):
        raise HTTPException(status_code=503, detail="mcp_client_enabled is off")
    server = next((s for s in mcp_client.list_mcp_servers() if s.get("id") == server_id), None)
    if not server:
        raise HTTPException(status_code=404, detail="server not found")
    return mcp_client.probe_server(server)


@router.post("/mcp/call")
def call_mcp(body: McpCallRequest) -> dict:
    settings = get_settings()
    return mcp_client.call_tool_readonly(
        body.server_id,
        body.tool_name,
        body.arguments,
        settings=settings,
    )
