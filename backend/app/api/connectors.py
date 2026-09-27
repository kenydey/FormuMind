"""Builtin connectors + MCP settings / import API."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import connectors_builtin, mcp_client, mcp_import

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


class McpImportJsonBody(BaseModel):
    json_text: str | None = None
    config: dict[str, Any] | None = None
    dry_run: bool = True


class McpImportGithubBody(BaseModel):
    url: str
    ref: str | None = None
    path: str | None = None
    dry_run: bool = True


class McpImportConfirmBody(BaseModel):
    import_id: str


class McpServerEnableBody(BaseModel):
    enabled: bool


def _import_error(payload: dict) -> HTTPException:
    msg = str(payload.get("detail") or "导入失败")
    return HTTPException(status_code=400, detail={"message": msg, **payload})


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


# ── Import first (static paths before /mcp/{server_id}) ─────────────────────


@router.post("/mcp/import")
def mcp_import_json(body: McpImportJsonBody) -> dict:
    if body.config is not None:
        text = json.dumps(body.config, ensure_ascii=False)
    elif body.json_text:
        text = body.json_text
    else:
        raise HTTPException(status_code=400, detail="需要 json_text 或 config")
    result = mcp_import.import_from_json_text(text, dry_run=body.dry_run, source="local")
    payload = mcp_import.result_to_dict(result)
    if not result.ok:
        raise _import_error(payload)
    return payload


@router.post("/mcp/import/github")
def mcp_import_github(body: McpImportGithubBody) -> dict:
    result = mcp_import.import_from_github(
        body.url, ref=body.ref, path=body.path, dry_run=body.dry_run
    )
    payload = mcp_import.result_to_dict(result)
    if not result.ok:
        raise _import_error(payload)
    return payload


@router.post("/mcp/import/upload")
async def mcp_import_upload(
    file: UploadFile = File(...),
    dry_run: bool = True,
) -> dict:
    data = await file.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"无法解码 JSON: {exc}") from exc
    result = mcp_import.import_from_json_text(text, dry_run=dry_run, source="local")
    payload = mcp_import.result_to_dict(result)
    if not result.ok:
        raise _import_error(payload)
    return payload


@router.post("/mcp/import/confirm")
def mcp_import_confirm(body: McpImportConfirmBody) -> dict:
    result = mcp_import.confirm_import(body.import_id)
    payload = mcp_import.result_to_dict(result)
    if not result.ok:
        raise _import_error(payload)
    return payload


@router.put("/mcp")
def replace_mcp(body: McpServersReplace) -> dict:
    settings = get_settings()
    if not bool(getattr(settings, "mcp_client_enabled", False)):
        raise HTTPException(status_code=503, detail="mcp_client_enabled is off")
    servers = mcp_client.save_mcp_servers([s.model_dump() for s in body.servers])
    return {"mcp": servers}


@router.patch("/mcp/{server_id}")
def patch_mcp_server(server_id: str, body: McpServerEnableBody) -> dict:
    """Toggle enabled flag without requiring mcp_client_enabled (config-only)."""
    servers = mcp_client.list_mcp_servers()
    hit = next((s for s in servers if s.get("id") == server_id), None)
    if not hit:
        raise HTTPException(status_code=404, detail="server not found")
    hit["enabled"] = bool(body.enabled)
    saved = mcp_client.save_mcp_servers(servers)
    return {"mcp": saved}


@router.delete("/mcp/{server_id}")
def delete_mcp_server(server_id: str) -> dict:
    current = mcp_client.list_mcp_servers()
    if not any(s.get("id") == server_id for s in current):
        raise HTTPException(status_code=404, detail="server not found")
    saved = mcp_client.save_mcp_servers([s for s in current if s.get("id") != server_id])
    return {"mcp": saved}


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
