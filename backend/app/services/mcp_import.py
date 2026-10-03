"""MCP server config import: Claude/Cursor JSON · upload · GitHub → dry_run → confirm."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from .http_safe import make_client

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,62}$")
MAX_JSON_BYTES = 256 * 1024

_SHELLISH = frozenset({"bash", "sh", "zsh", "cmd", "cmd.exe", "powershell", "pwsh"})
_WARN_ARGS = (
    re.compile(r"(^|/)\.ssh(/|$)"),
    re.compile(r"(^|/)etc/passwd"),
)

FetchFn = Callable[[str], bytes]


@dataclass
class McpServerPreview:
    id: str
    command: str
    args: list[str] = field(default_factory=list)
    env_keys: list[str] = field(default_factory=list)
    transport: str = "stdio"
    enabled: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class McpImportPreview:
    servers: list[McpServerPreview] = field(default_factory=list)
    source: str = "local"
    source_url: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class McpImportResult:
    ok: bool
    dry_run: bool
    import_id: str | None = None
    preview: McpImportPreview | None = None
    imported: bool = False
    detail: str = ""
    mcp: list[dict[str, Any]] = field(default_factory=list)


def _data_root() -> Path:
    return Path("./data").resolve()


def pending_dir() -> Path:
    return _data_root() / "mcp_pending"


def ledger_path() -> Path:
    return _data_root() / "mcp_import_ledger.json"


def _default_fetch(url: str) -> bytes:
    headers = {
        "User-Agent": "FormuMind-mcp-import",
        "Accept": "application/vnd.github+json, application/json, text/plain",
    }
    with make_client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        r = client.get(url)
        if r.status_code >= 400:
            raise ValueError(f"请求失败 {r.status_code}: {url}")
        if len(r.content) > MAX_JSON_BYTES * 2:
            raise ValueError("远程内容过大")
        return r.content


def parse_github_repo_ref(url: str) -> dict[str, str]:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("GitHub URL 不能为空")
    owner = repo = path = ""
    ref = "main"
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        if parsed.netloc not in {"github.com", "www.github.com"}:
            raise ValueError("仅支持 github.com 地址")
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            raise ValueError("URL 需包含 owner/repo")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
            ref = parts[3]
            path = "/".join(parts[4:])
        elif len(parts) > 2:
            path = "/".join(parts[2:])
    else:
        at_ref = ""
        if "@" in raw:
            raw, at_ref = raw.rsplit("@", 1)
            ref = at_ref.strip() or ref
        parts = [p for p in raw.split("/") if p]
        if len(parts) < 2:
            raise ValueError("格式应为 owner/repo[/path][@ref]")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        path = "/".join(parts[2:])
    return {"owner": owner, "repo": repo, "ref": ref, "path": path.strip("/")}


def _normalize_server(sid: str, cfg: dict[str, Any]) -> McpServerPreview:
    preview = McpServerPreview(id=sid, command="")
    if not ID_RE.match(sid):
        preview.errors.append(f"非法服务器 id「{sid}」")
    cmd = cfg.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        preview.errors.append(f"{sid}: 缺少 command")
    else:
        if "\n" in cmd or "\r" in cmd:
            preview.errors.append(f"{sid}: command 不能含换行")
        preview.command = cmd.strip()

    args_raw = cfg.get("args") or cfg.get("argv") or []
    if args_raw is None:
        args_raw = []
    if not isinstance(args_raw, list) or not all(isinstance(a, (str, int, float)) for a in args_raw):
        preview.errors.append(f"{sid}: args 须为字符串列表")
        args: list[str] = []
    else:
        args = [str(a) for a in args_raw]
    preview.args = args

    env = cfg.get("env") or {}
    if env and not isinstance(env, dict):
        preview.errors.append(f"{sid}: env 须为对象")
        env = {}
    env_str = {str(k): str(v) for k, v in dict(env).items()}
    preview.env_keys = sorted(env_str.keys())

    transport = str(cfg.get("transport") or "stdio").strip() or "stdio"
    if transport != "stdio":
        preview.errors.append(f"{sid}: 仅支持 stdio transport（收到 {transport}）")
    preview.transport = "stdio"
    # Import always lands disabled — user must explicitly enable.
    preview.enabled = False

    base = Path(preview.command).name.lower() if preview.command else ""
    if base in _SHELLISH:
        preview.warnings.append(f"{sid}: command 为 shell（{base}）— 请确认 args 安全")
    for a in args:
        for re_pat in _WARN_ARGS:
            if re_pat.search(a):
                preview.warnings.append(f"{sid}: 可疑参数 {a}")
    if any(str(a) in {"-c", "/c"} for a in args):
        preview.warnings.append(f"{sid}: 含 -c /c 内联执行参数")

    return preview


def parse_mcp_config(data: Any) -> McpImportPreview:
    preview = McpImportPreview()
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            preview.errors.append(f"JSON 解析失败: {exc}")
            return preview

    if not isinstance(data, dict):
        preview.errors.append("根节点须为 JSON 对象")
        return preview

    servers_map: dict[str, Any] = {}

    if isinstance(data.get("mcpServers"), dict):
        servers_map = dict(data["mcpServers"])
    elif isinstance(data.get("servers"), dict):
        servers_map = dict(data["servers"])
    elif isinstance(data.get("mcp"), dict) and isinstance(data["mcp"].get("servers"), dict):
        servers_map = dict(data["mcp"]["servers"])
    elif isinstance(data.get("servers"), list):
        for item in data["servers"]:
            if isinstance(item, dict) and item.get("id"):
                servers_map[str(item["id"])] = item
    elif isinstance(data.get("mcp_servers"), list):
        for item in data["mcp_servers"]:
            if isinstance(item, dict) and item.get("id"):
                servers_map[str(item["id"])] = item
    else:
        # Single server object with command
        if data.get("command") and (data.get("id") or data.get("name")):
            sid = str(data.get("id") or data.get("name"))
            servers_map[sid] = data
        else:
            preview.errors.append(
                "未识别的 MCP 配置形状（支持 mcpServers / servers / mcp.servers / 数组）"
            )
            return preview

    if not servers_map:
        preview.errors.append("未找到任何 MCP 服务器条目")
        return preview

    for sid, cfg in servers_map.items():
        if not isinstance(cfg, dict):
            preview.errors.append(f"{sid}: 配置须为对象")
            continue
        sp = _normalize_server(str(sid), cfg)
        preview.servers.append(sp)
        preview.errors.extend(sp.errors)
        preview.warnings.extend(sp.warnings)

    if not preview.servers:
        preview.errors.append("没有可导入的服务器")
    return preview


def _servers_payload(preview: McpImportPreview, raw_cfgs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sp in preview.servers:
        if sp.errors:
            continue
        cfg = raw_cfgs.get(sp.id) or {}
        env = cfg.get("env") or {}
        env_str = {str(k): str(v) for k, v in dict(env).items()} if isinstance(env, dict) else {}
        out.append(
            {
                "id": sp.id,
                "command": sp.command,
                "args": list(sp.args),
                "env": env_str,
                "enabled": False,
                "transport": "stdio",
            }
        )
    return out


def _extract_raw_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    preview = parse_mcp_config(data)
    raw: dict[str, dict[str, Any]] = {}
    if isinstance(data.get("mcpServers"), dict):
        raw = {str(k): v for k, v in data["mcpServers"].items() if isinstance(v, dict)}
    elif isinstance(data.get("servers"), dict):
        raw = {str(k): v for k, v in data["servers"].items() if isinstance(v, dict)}
    elif isinstance(data.get("mcp"), dict) and isinstance(data["mcp"].get("servers"), dict):
        raw = {str(k): v for k, v in data["mcp"]["servers"].items() if isinstance(v, dict)}
    elif isinstance(data.get("servers"), list):
        for item in data["servers"]:
            if isinstance(item, dict) and item.get("id"):
                raw[str(item["id"])] = item
    elif isinstance(data.get("mcp_servers"), list):
        for item in data["mcp_servers"]:
            if isinstance(item, dict) and item.get("id"):
                raw[str(item["id"])] = item
    elif data.get("command") and (data.get("id") or data.get("name")):
        sid = str(data.get("id") or data.get("name"))
        raw[sid] = data
    # Ensure keys match preview ids
    _ = preview
    return raw


def _write_pending(payload: dict[str, Any]) -> str:
    pending_dir().mkdir(parents=True, exist_ok=True)
    import_id = uuid.uuid4().hex
    path = pending_dir() / f"{import_id}.json"
    payload = {**payload, "import_id": import_id, "created_at": time.time()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return import_id


def _load_pending(import_id: str) -> dict[str, Any]:
    path = pending_dir() / f"{import_id}.json"
    if not path.is_file():
        raise FileNotFoundError("import_id 无效或已过期")
    return json.loads(path.read_text(encoding="utf-8"))


def _clear_pending(import_id: str) -> None:
    path = pending_dir() / f"{import_id}.json"
    if path.is_file():
        path.unlink()


def _append_ledger(entry: dict[str, Any]) -> None:
    with _LOCK:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        rows: list[Any] = []
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    rows = data
            except json.JSONDecodeError:
                rows = []
        rows.append(entry)
        path.write_text(json.dumps(rows[-200:], ensure_ascii=False, indent=2), encoding="utf-8")


def _finalize(
    *,
    data: dict[str, Any],
    preview: McpImportPreview,
    dry_run: bool,
    source: str,
    source_url: str = "",
) -> McpImportResult:
    preview.source = source
    preview.source_url = source_url
    if preview.errors:
        return McpImportResult(
            ok=False,
            dry_run=dry_run,
            preview=preview,
            detail="; ".join(preview.errors),
        )
    raw_map = _extract_raw_map(data)
    servers = _servers_payload(preview, raw_map)
    if not servers:
        return McpImportResult(
            ok=False,
            dry_run=dry_run,
            preview=preview,
            detail="没有通过审查的服务器",
        )

    pending_body = {
        "servers": servers,
        "source": source,
        "source_url": source_url,
        "preview": asdict(preview),
    }
    if dry_run:
        import_id = _write_pending(pending_body)
        return McpImportResult(
            ok=True,
            dry_run=True,
            import_id=import_id,
            preview=preview,
            detail="预览通过，确认后写入（默认禁用，需手动启用）",
        )

    from . import mcp_client

    existing = {s["id"]: s for s in mcp_client.list_mcp_servers()}
    for s in servers:
        existing[s["id"]] = s
    saved = mcp_client.save_mcp_servers(list(existing.values()))
    _append_ledger(
        {
            "ids": [s["id"] for s in servers],
            "source": source,
            "source_url": source_url,
            "verdict": "imported",
            "at": time.time(),
            "warnings": preview.warnings,
        }
    )
    return McpImportResult(
        ok=True,
        dry_run=False,
        preview=preview,
        imported=True,
        mcp=saved,
        detail="已导入（服务器默认禁用；开启 mcp_client_enabled 后可 Probe/启用）",
    )


def import_from_json_text(
    text: str,
    *,
    dry_run: bool = True,
    source: str = "local",
    source_url: str = "",
) -> McpImportResult:
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        prev = McpImportPreview()
        prev.errors.append(f"JSON 超过 {MAX_JSON_BYTES} 字节上限")
        return McpImportResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        prev = McpImportPreview()
        prev.errors.append(f"JSON 解析失败: {exc}")
        return McpImportResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])
    if not isinstance(data, dict):
        prev = McpImportPreview()
        prev.errors.append("根节点须为 JSON 对象")
        return McpImportResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])
    preview = parse_mcp_config(data)
    return _finalize(
        data=data, preview=preview, dry_run=dry_run, source=source, source_url=source_url
    )


def import_from_github(
    url: str,
    *,
    ref: str | None = None,
    path: str | None = None,
    dry_run: bool = True,
    fetch: FetchFn | None = None,
) -> McpImportResult:
    fetch = fetch or _default_fetch
    try:
        loc = parse_github_repo_ref(url)
    except ValueError as exc:
        prev = McpImportPreview()
        prev.errors.append(str(exc))
        return McpImportResult(ok=False, dry_run=dry_run, preview=prev, detail=str(exc))
    if ref:
        loc["ref"] = ref
    if path is not None and str(path).strip():
        loc["path"] = str(path).strip().strip("/")

    candidates: list[str] = []
    if loc["path"]:
        candidates.append(loc["path"])
        if not loc["path"].endswith(".json"):
            candidates.extend(
                [
                    f"{loc['path'].rstrip('/')}/mcp.json",
                    f"{loc['path'].rstrip('/')}/.cursor/mcp.json",
                ]
            )
    else:
        candidates.extend(
            [
                "mcp.json",
                ".cursor/mcp.json",
                ".vscode/mcp.json",
                "connectors.json",
            ]
        )

    last_err = "未找到 mcp.json"
    for rel in candidates:
        raw_url = (
            f"https://raw.githubusercontent.com/{loc['owner']}/{loc['repo']}/{loc['ref']}/{rel}"
        )
        try:
            body = fetch(raw_url).decode("utf-8")
            source_url = (
                f"https://github.com/{loc['owner']}/{loc['repo']}/blob/{loc['ref']}/{rel}"
            )
            return import_from_json_text(
                body, dry_run=dry_run, source="github", source_url=source_url
            )
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            continue

    prev = McpImportPreview()
    prev.errors.append(f"拉取失败: {last_err}")
    return McpImportResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])


def confirm_import(import_id: str) -> McpImportResult:
    try:
        payload = _load_pending(import_id)
    except FileNotFoundError as exc:
        return McpImportResult(ok=False, dry_run=False, detail=str(exc))

    servers = list(payload.get("servers") or [])
    if not servers:
        return McpImportResult(ok=False, dry_run=False, detail="pending 无服务器")

    # Re-validate
    data = {"servers": servers}
    preview = parse_mcp_config(data)
    # parse of array form may mark enabled false already
    if preview.errors:
        return McpImportResult(
            ok=False,
            dry_run=False,
            import_id=import_id,
            preview=preview,
            detail="; ".join(preview.errors),
        )

    from . import mcp_client

    existing = {s["id"]: s for s in mcp_client.list_mcp_servers()}
    for s in servers:
        s = dict(s)
        s["enabled"] = False
        existing[s["id"]] = s
    saved = mcp_client.save_mcp_servers(list(existing.values()))
    _append_ledger(
        {
            "ids": [s["id"] for s in servers],
            "source": payload.get("source"),
            "source_url": payload.get("source_url"),
            "verdict": "imported",
            "at": time.time(),
        }
    )
    _clear_pending(import_id)
    if preview.source == "local" and payload.get("source"):
        preview.source = str(payload.get("source"))
    preview.source_url = str(payload.get("source_url") or "")
    return McpImportResult(
        ok=True,
        dry_run=False,
        import_id=import_id,
        preview=preview,
        imported=True,
        mcp=saved,
        detail="已导入（服务器默认禁用）",
    )


def result_to_dict(result: McpImportResult) -> dict[str, Any]:
    d: dict[str, Any] = {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "import_id": result.import_id,
        "imported": result.imported,
        "detail": result.detail,
        "mcp": result.mcp,
    }
    if result.preview:
        d["preview"] = asdict(result.preview)
    return d
