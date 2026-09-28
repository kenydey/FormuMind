"""Generate SKILL.md docs from enabled MCP servers for chat prompt inject."""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 调用约束：写进每份 skill 文档顶部，防止模型猜工具名或绕过 host.mcp。
_CONSTRAINT_LINES = (
    "先加载本 skill，再按本文档调用工具；绝不猜测工具名。",
    "工具调用只走 host.mcp；禁止 raw HTTP 直连外部服务。",
)

_SAFE_ID_RE = re.compile(r"^[a-z0-9-]+$")

# schema 类型 → 示例占位（仅用于文档示例，不代表真实值）。
_TYPE_PLACEHOLDERS: dict[str, Any] = {
    "string": "string",
    "integer": 0,
    "number": 0,
    "boolean": True,
    "array": [],
    "object": {},
}


def _skills_root() -> Path:
    return Path("./data").resolve() / "skills"


def _safe_id(server_id: str) -> str:
    """skill 目录名：小写化后仅保留 a-z0-9-，其余字符折叠为单个 '-'。"""
    cleaned = re.sub(r"[^a-z0-9]+", "-", (server_id or "").strip().lower())
    cleaned = cleaned.strip("-")[:64]
    return cleaned or "mcp"


def skill_id_for_server(server_id: str) -> str:
    return f"mcp-{_safe_id(server_id)}"


def _normalize_descriptors(probed: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """把 probe 结果统一为 descriptors；兼容旧 shape（tools 为纯名称列表）。"""
    descriptors: list[dict[str, Any]] = []
    names: list[str] = []
    for t in probed.get("tools") or []:
        if isinstance(t, str):
            names.append(t)
            descriptors.append({"name": t, "description": "", "inputSchema": {}})
            continue
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
    # 旧 shape 只有 names 时补一份空描述符，保证渲染不丢工具名。
    for n in probed.get("names") or []:
        if n and str(n) not in names:
            names.append(str(n))
            descriptors.append({"name": str(n), "description": "", "inputSchema": {}})
    return descriptors, names


def _example_value(prop: dict[str, Any]) -> Any:
    """示例值优先级：enum[0] → default → 类型占位；绝不虚构 schema 外字段。"""
    if not isinstance(prop, dict):
        return None
    enum = prop.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    if "default" in prop:
        return prop["default"]
    return _TYPE_PLACEHOLDERS.get(str(prop.get("type") or ""), None)


def _render_tool_section(desc: dict[str, Any]) -> str:
    name = str(desc.get("name") or "")
    description = str(desc.get("description") or "").strip() or "（暂无描述）"
    schema = desc.get("inputSchema")
    schema = schema if isinstance(schema, dict) else {}
    props = schema.get("properties")
    props = props if isinstance(props, dict) else {}
    required = schema.get("required")
    required = set(required) if isinstance(required, list) else set()

    lines = [
        f"### {name}",
        "",
        description,
        "",
        "**Input**（仅列出 required 或含 default 的字段）：",
        "",
    ]
    example: dict[str, Any] = {}
    shown = False
    for fname, prop in props.items():
        if not isinstance(prop, dict):
            continue
        is_required = fname in required
        has_default = "default" in prop
        if not (is_required or has_default):
            continue
        shown = True
        ptype = str(prop.get("type") or "any")
        req_mark = "required" if is_required else "optional"
        detail = f"`{fname}` ({ptype}, {req_mark})"
        extras: list[str] = []
        if has_default:
            extras.append(f"default={json.dumps(prop['default'], ensure_ascii=False)}")
        enum = prop.get("enum")
        if isinstance(enum, list) and enum:
            extras.append(f"enum={json.dumps(enum, ensure_ascii=False)}")
        if extras:
            detail += ": " + " / ".join(extras)
        lines.append(f"- {detail}")
        example[fname] = _example_value(prop)
    if not shown:
        lines.append("- _(无输入参数)_")
    lines += [
        "",
        "**Example**:",
        "",
        "```json",
        json.dumps(example, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def _trigger_description(server: dict[str, Any], sid: str) -> str:
    """触发式描述：优先读 server 配置的 useWhen，无则用通用文案。"""
    use_when = str(server.get("useWhen") or "").strip()
    if use_when:
        return f"当用户需要{use_when}时加载本 skill；先加载 skill，再按文档调用工具。"
    return "当用户需要该 MCP 服务器提供的外部工具能力时加载本 skill；先加载 skill，再按文档调用工具。"


def _render_skill_doc(
    *,
    skill_id: str,
    server: dict[str, Any],
    sid: str,
    descriptors: list[dict[str, Any]],
) -> str:
    trigger = _trigger_description(server, sid)
    lines = [
        "---",
        f"name: {skill_id}",
        f"summary: MCP server {sid}",
        f"description: {trigger}",
        "category: mcp",
        "activation_policy: user-controlled",
        "allowed_tools:",
        "entry: true",
        "---",
        "",
        f"# MCP · {sid}",
        "",
        trigger,
        "",
        "## 调用约束",
        "",
    ]
    lines.extend(f"- {c}" for c in _CONSTRAINT_LINES)
    lines += ["", "## Tools", ""]
    if descriptors:
        for desc in descriptors:
            lines.append(_render_tool_section(desc))
    else:
        lines.append("- _(probe 成功但无工具)_")
    return "\n".join(lines).rstrip("\n") + "\n"


# P-9: probe-result cache. W5-5 caches the MCP *session* (subprocess) per
# server, but every chat request still sent a fresh `tools/list` roundtrip
# via probe_server. Cache the probe *result* keyed by (server_id, generation):
# a generation bump (reconnect / config change) invalidates automatically.
# Failures are never cached — the next request retries. TTL is a safety net
# for servers whose tool list changes without a config change.
_PROBE_CACHE: dict[str, tuple[int, float, dict[str, Any]]] = {}
_PROBE_CACHE_TTL_S = 300.0
_PROBE_CACHE_LOCK = threading.Lock()


def _probe_generation(server_id: str) -> int:
    try:
        from .mcp_client import _CONFIG_GENERATIONS

        return int(_CONFIG_GENERATIONS.get(server_id, 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def invalidate_mcp_probe_cache(server_id: str | None = None) -> None:
    """Drop cached probe results (all, or one server)."""
    with _PROBE_CACHE_LOCK:
        if server_id is None:
            _PROBE_CACHE.clear()
        else:
            _PROBE_CACHE.pop(str(server_id), None)


def _cached_probe(server: dict[str, Any]) -> dict[str, Any]:
    """probe_server with (server_id, generation, probe callable) result caching."""
    from .mcp_client import probe_server

    sid = str(server.get("id") or "")
    gen = _probe_generation(sid)
    # P-9: 把 probe callable identity 纳入缓存命中判断。生产环境
    # probe_server 是稳定的模块函数，identity 不变 → 正常命中；测试中每次
    # monkeypatch 装入不同函数 → 自动失效，避免跨测试复用旧描述符。
    probe_id = id(probe_server)
    now = time.monotonic()
    with _PROBE_CACHE_LOCK:
        entry = _PROBE_CACHE.get(sid)
        if (
            entry is not None
            and entry[0] == gen
            and entry[1] == probe_id
            and now - entry[2] < _PROBE_CACHE_TTL_S
        ):
            return entry[3]
    probed = probe_server(server)
    with _PROBE_CACHE_LOCK:
        if probed.get("ok"):
            # Re-read generation: the probe itself may have bumped it
            # (reconnect path in _acquire_session).
            _PROBE_CACHE[sid] = (_probe_generation(sid), probe_id, time.monotonic(), probed)
        else:
            _PROBE_CACHE.pop(sid, None)
    return probed


def ensure_mcp_skill_docs(
    *,
    server_ids: list[str] | None = None,
    settings: Any = None,
    probe: bool = True,
) -> list[dict[str, Any]]:
    """Write ``data/skills/mcp-<id>/SKILL.md`` for enabled MCP servers.

    Returns list of {server_id, skill_id, tools, path, ok}.
    probe 失败时删除旧 skill 目录（防过期工具广告），不写新文档。
    """
    from .mcp_client import list_mcp_servers

    if settings is not None and not bool(getattr(settings, "mcp_client_enabled", False)):
        return []

    wanted = {s.strip() for s in (server_ids or []) if s and s.strip()}
    out: list[dict[str, Any]] = []
    seen_safe: dict[str, str] = {}  # safe_id -> server_id（本轮已处理）
    for server in list_mcp_servers():
        if not server.get("enabled"):
            continue
        sid = str(server.get("id") or "")
        if wanted and sid not in wanted:
            continue
        safe = _safe_id(sid)
        assert _SAFE_ID_RE.match(safe), f"unsafe skill id: {safe!r}"
        skill_id = f"mcp-{safe}"
        skill_dir = _skills_root() / skill_id

        # 大小写折叠冲突（本轮两个 server 映射到同一 skill id）→ 跳过并记 error，不覆盖。
        owner = seen_safe.get(safe)
        if owner is not None and owner != sid:
            msg = (
                f"skill id 冲突：'{sid}' 与 '{owner}' 折叠为同一 skill id "
                f"'{skill_id}'，已跳过（不覆盖已有 skill）"
            )
            logger.error(msg)
            out.append(
                {
                    "server_id": sid,
                    "skill_id": skill_id,
                    "tools": [],
                    "path": "",
                    "ok": False,
                    "error": msg,
                }
            )
            continue
        seen_safe[safe] = sid

        # 已有目录归属其他 server（历史命名冲突）→ 不覆盖。
        meta_path = skill_dir / ".formumind-install.json"
        if meta_path.is_file():
            try:
                prev = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                prev = {}
            prev_sid = prev.get("server_id")
            if prev_sid not in (None, sid):
                msg = (
                    f"skill 目录 {skill_dir} 已归属 server '{prev_sid}'，"
                    f"跳过 '{sid}'（不覆盖）"
                )
                logger.error(msg)
                out.append(
                    {
                        "server_id": sid,
                        "skill_id": skill_id,
                        "tools": [],
                        "path": "",
                        "ok": False,
                        "error": msg,
                    }
                )
                continue

        tools: list[str] = []
        err = None
        if probe:
            try:
                # P-9: cached probe — one tools/list roundtrip per
                # (server, generation), not one per chat request.
                probed = _cached_probe(server)
                descriptors, tools = _normalize_descriptors(probed)
                if not probed.get("ok"):
                    err = probed.get("error")
            except Exception as exc:  # noqa: BLE001
                err = str(exc)[:200]
                descriptors = []
        else:
            descriptors = []

        if err:
            # probe 失败 → 删除旧目录，避免过期工具广告；不写新文档。
            if skill_dir.exists():
                shutil.rmtree(skill_dir, ignore_errors=True)
                logger.warning(
                    "MCP probe 失败，已删除过期 skill 目录：%s（%s）", skill_dir, err
                )
            out.append(
                {
                    "server_id": sid,
                    "skill_id": skill_id,
                    "tools": [],
                    "path": str(skill_dir / "SKILL.md"),
                    "ok": False,
                    "error": err,
                }
            )
            continue

        body = _render_skill_doc(
            skill_id=skill_id, server=server, sid=sid, descriptors=descriptors
        )
        path = skill_dir / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        # Origin marker so chat_skills treats as pack/local
        meta_path.write_text(
            json.dumps({"origin": "pack", "source": "mcp", "server_id": sid}),
            encoding="utf-8",
        )
        out.append(
            {
                "server_id": sid,
                "skill_id": skill_id,
                "tools": tools,
                "path": str(path),
                "ok": True,
                "error": None,
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
