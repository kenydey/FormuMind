"""Generate SKILL.md docs from enabled MCP servers for chat prompt inject."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _skills_root() -> Path:
    return Path("./data").resolve() / "skills"


def _safe_id(server_id: str) -> str:
    return re.sub(r"[^\w.\-]+", "-", (server_id or "").strip())[:64] or "mcp"


def skill_id_for_server(server_id: str) -> str:
    return f"mcp-{_safe_id(server_id)}"


def ensure_mcp_skill_docs(
    *,
    server_ids: list[str] | None = None,
    settings: Any = None,
    probe: bool = True,
) -> list[dict[str, Any]]:
    """Write ``data/skills/mcp-<id>/SKILL.md`` for enabled MCP servers.

    Returns list of {server_id, skill_id, tools, path, ok}.
    """
    from .mcp_client import list_mcp_servers, probe_server

    if settings is not None and not bool(getattr(settings, "mcp_client_enabled", False)):
        return []

    wanted = {s.strip() for s in (server_ids or []) if s and s.strip()}
    out: list[dict[str, Any]] = []
    for server in list_mcp_servers():
        if not server.get("enabled"):
            continue
        sid = str(server.get("id") or "")
        if wanted and sid not in wanted:
            continue
        tools: list[str] = []
        err = None
        if probe:
            try:
                probed = probe_server(server)
                tools = list(probed.get("tools") or [])
                if not probed.get("ok"):
                    err = probed.get("error")
            except Exception as exc:  # noqa: BLE001
                err = str(exc)[:200]
        skill_id = skill_id_for_server(sid)
        path = _skills_root() / skill_id / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        tool_lines = "\n".join(f"- `{t}`" for t in tools) or "- _(probe failed or empty)_"
        body = (
            f"---\n"
            f"name: {skill_id}\n"
            f"summary: MCP server {sid}\n"
            f"description: Tools exposed by MCP server '{sid}' for formula research.\n"
            f"category: mcp\n"
            f"activation_policy: user-controlled\n"
            f"allowed_tools:\n"
            f"entry: true\n"
            f"---\n\n"
            f"# MCP · {sid}\n\n"
            f"This skill documents tools from the custom MCP server `{sid}`.\n"
            f"Readonly tools may be invoked when selected in chat; write-like "
            f"tools require an explicit session approval.\n\n"
            f"## Tools\n\n{tool_lines}\n"
        )
        if err:
            body += f"\n> Probe note: {err}\n"
        path.write_text(body, encoding="utf-8")
        # Origin marker so chat_skills treats as pack/local
        meta = path.parent / ".formumind-install.json"
        meta.write_text(
            '{"origin":"pack","source":"mcp","server_id":"%s"}' % sid,
            encoding="utf-8",
        )
        out.append(
            {
                "server_id": sid,
                "skill_id": skill_id,
                "tools": tools,
                "path": str(path),
                "ok": err is None,
                "error": err,
            }
        )
    return out


def mcp_skill_ids(server_ids: list[str]) -> list[str]:
    return [skill_id_for_server(s) for s in server_ids if s]


def mcp_prompt_block(server_ids: list[str], *, settings: Any = None) -> str:
    """Ensure docs exist and return skill_prompt_block for mcp-* skills."""
    if not server_ids:
        return ""
    try:
        ensure_mcp_skill_docs(server_ids=server_ids, settings=settings, probe=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("mcp skill-doc ensure failed: %s", exc)
    from .chat_skills import skill_prompt_block

    return skill_prompt_block(mcp_skill_ids(server_ids))
