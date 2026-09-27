"""Skill package install: GitHub / zip / paste → dry_run preview → confirm.

Security review is a light FormuMind subset of SynSci/AIPOCH install gates.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .chat_skills import ALLOWED_CHAT_TOOLS, parse_frontmatter

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")
MAX_MARKDOWN_BYTES = 512 * 1024
MAX_ZIP_BYTES = 2 * 1024 * 1024
MAX_TOTAL_TEXT_BYTES = 2 * 1024 * 1024
MAX_FILES = 40

_REJECT_DESC = [
    re.compile(r"always\s+run\s+this\s+skill", re.I),
    re.compile(r"must\s+always\s+run", re.I),
    re.compile(r"ignore\s+(prior|previous|above)\s+instructions", re.I),
]
_REJECT_BODY = [
    re.compile(r"\brm\s+-rf\s+/(\*)?(\s|$)"),
    re.compile(r":\(\)\s*\{\s*:|:&\s*\}\s*;\s*:"),
    re.compile(r"bash\s+-i\s+>&\s+/dev/tcp/"),
    re.compile(r"nc\s+-e\s+/bin/(sh|bash)"),
    re.compile(r"respond\s+with\s+verdict\s*:?\s*(pass|safe|approve)", re.I),
    re.compile(r"ignore.*(prior|previous|above).*(instructions|prompt)", re.I),
]
_WARN_BODY = [
    (re.compile(r"\bcurl\b[^\n|]*\|\s*(sh|bash)\b"), "curl | sh"),
    (re.compile(r"\bwget\b[^\n|]*\|\s*(sh|bash)\b"), "wget | sh"),
    (re.compile(r"~/\.ssh\b"), "~/.ssh"),
    (re.compile(r"\beval\s+(?:\$\(|`)"), "eval"),
]

FetchFn = Callable[[str], bytes]


@dataclass
class ReviewFinding:
    level: str  # error | warning
    message: str


@dataclass
class SkillPreview:
    name: str
    description: str = ""
    summary: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    rejected_tools: list[str] = field(default_factory=list)
    origin: str = "local"
    source_url: str = ""
    pinned_sha: str = ""
    file_count: int = 1
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class InstallResult:
    ok: bool
    dry_run: bool
    install_id: str | None = None
    skill_id: str | None = None
    preview: SkillPreview | None = None
    installed: bool = False
    detail: str = ""


def _data_root() -> Path:
    return Path("./data").resolve()


def skills_dir() -> Path:
    return _data_root() / "skills"


def pending_dir() -> Path:
    return _data_root() / "skills_pending"


def ledger_path() -> Path:
    return _data_root() / "skills_install_ledger.json"


def install_meta_path(skill_name: str) -> Path:
    return skills_dir() / skill_name / ".formumind-install.json"


def _bundled_skill_ids() -> set[str]:
    from ..resources.formulation_skills import list_formulation_skills

    ids = {s["id"] for s in list_formulation_skills()}
    root = Path(__file__).resolve().parents[1] / "resources" / "chat_skills"
    if root.is_dir():
        for p in root.glob("*/SKILL.md"):
            try:
                fields, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
                ids.add(fields.get("name") or p.parent.name)
            except OSError:
                ids.add(p.parent.name)
    return ids


def parse_github_skill_url(url: str) -> dict[str, str]:
    """Parse GitHub skill location from URL or owner/repo[/path][@ref]."""
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
        # owner/repo[/path][@ref] or owner/repo@ref
        at_ref = ""
        if "@" in raw:
            raw, at_ref = raw.rsplit("@", 1)
            ref = at_ref.strip() or ref
        parts = [p for p in raw.split("/") if p]
        if len(parts) < 2:
            raise ValueError("格式应为 owner/repo[/path][@ref]")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        path = "/".join(parts[2:])

    path = path.strip("/")
    if path.endswith("SKILL.md"):
        path = path[: -len("SKILL.md")].rstrip("/")

    return {"owner": owner, "repo": repo, "ref": ref, "path": path}


def _default_fetch(url: str) -> bytes:
    import httpx

    headers = {
        "User-Agent": "FormuMind-skill-install",
        "Accept": "application/vnd.github+json",
    }
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        r = client.get(url)
        if r.status_code >= 400:
            raise ValueError(f"GitHub 请求失败 {r.status_code}: {url}")
        data = r.content
        if len(data) > MAX_ZIP_BYTES * 2:
            raise ValueError("远程内容过大")
        return data


def fetch_github_skill_markdown(
    location: dict[str, str],
    *,
    fetch: FetchFn | None = None,
) -> tuple[str, str]:
    """Return (markdown, pinned_sha_hint). Uses Contents API then raw fallback."""
    fetch = fetch or _default_fetch
    owner, repo, ref, path = location["owner"], location["repo"], location["ref"], location["path"]
    api_path = f"{path}/SKILL.md" if path else "SKILL.md"
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{api_path}?ref={ref}"
    pinned = ""
    try:
        raw = fetch(api_url)
        meta = json.loads(raw.decode("utf-8"))
        if isinstance(meta, dict) and meta.get("encoding") == "base64" and meta.get("content"):
            import base64

            pinned = str(meta.get("sha") or "")
            md = base64.b64decode(meta["content"]).decode("utf-8")
            return md, pinned
        if isinstance(meta, list):
            # directory listing without SKILL.md at that path — search
            for item in meta:
                if isinstance(item, dict) and str(item.get("name") or "").lower() == "skill.md":
                    download = item.get("download_url") or item.get("url")
                    pinned = str(item.get("sha") or "")
                    if download:
                        body = fetch(str(download))
                        # nested API object?
                        try:
                            nested = json.loads(body.decode("utf-8"))
                            if nested.get("encoding") == "base64":
                                import base64

                                return base64.b64decode(nested["content"]).decode("utf-8"), pinned
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            return body.decode("utf-8"), pinned
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.info("contents API path failed, trying raw: %s", exc)

    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{api_path}"
    body = fetch(raw_url)
    return body.decode("utf-8"), pinned or ref


def review_skill_markdown(markdown: str, *, name_hint: str | None = None) -> SkillPreview:
    if len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES:
        prev = SkillPreview(name=name_hint or "invalid")
        prev.errors.append(f"SKILL.md 超过 {MAX_MARKDOWN_BYTES} 字节上限")
        return prev

    fields, body = parse_frontmatter(markdown)
    name = (name_hint or fields.get("name") or "").strip().lower()
    preview = SkillPreview(
        name=name,
        description=fields.get("description") or "",
        summary=fields.get("summary") or fields.get("description") or "",
    )
    if not fields:
        preview.errors.append("缺少 YAML frontmatter（需 --- 包裹的 name/description）")
    if not name:
        preview.errors.append("缺少 name（frontmatter 或参数）")
    elif not NAME_RE.match(name):
        preview.errors.append("name 须匹配 [a-z0-9][a-z0-9-]{0,62}")
    if not (fields.get("description") or fields.get("summary")):
        preview.errors.append("frontmatter 需要 description 或 summary")

    tools_raw = fields.get("allowed_tools") or ""
    tools = [t.strip() for t in re.split(r"[\s,]+", tools_raw) if t.strip()]
    rejected = [t for t in tools if t not in ALLOWED_CHAT_TOOLS]
    allowed = [t for t in tools if t in ALLOWED_CHAT_TOOLS]
    preview.allowed_tools = allowed
    preview.rejected_tools = rejected
    if any(t.lower() in {"shell", "bash", "exec", "os.system"} for t in tools):
        preview.errors.append("禁止 allowed_tools 含 shell/bash/exec")
    elif rejected:
        preview.errors.append(
            "allowed_tools 含未白名单工具: " + ", ".join(rejected)
        )

    desc = f"{fields.get('description', '')}\n{fields.get('summary', '')}"
    for re_pat in _REJECT_DESC:
        if re_pat.search(desc):
            preview.errors.append(f"描述命中危险模式: {re_pat.pattern}")
    hay = markdown
    for re_pat in _REJECT_BODY:
        if re_pat.search(hay):
            preview.errors.append(f"内容命中危险模式: {re_pat.pattern}")
    for re_pat, label in _WARN_BODY:
        if re_pat.search(hay):
            preview.warnings.append(f"可疑内容（{label}）— 请确认后安装")

    if name and name in _bundled_skill_ids():
        preview.errors.append(f"不能覆盖内置技能 id「{name}」")

    return preview


def _write_pending(payload: dict[str, Any]) -> str:
    pending_dir().mkdir(parents=True, exist_ok=True)
    install_id = uuid.uuid4().hex
    path = pending_dir() / f"{install_id}.json"
    payload = {**payload, "install_id": install_id, "created_at": time.time()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return install_id


def _load_pending(install_id: str) -> dict[str, Any]:
    path = pending_dir() / f"{install_id}.json"
    if not path.is_file():
        raise FileNotFoundError("install_id 无效或已过期")
    return json.loads(path.read_text(encoding="utf-8"))


def _clear_pending(install_id: str) -> None:
    path = pending_dir() / f"{install_id}.json"
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


def _materialize(name: str, markdown: str, meta: dict[str, Any]) -> Path:
    dest = skills_dir() / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(markdown, encoding="utf-8")
    meta_path = install_meta_path(name)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def _finalize(
    *,
    markdown: str,
    preview: SkillPreview,
    dry_run: bool,
    origin: str,
    source_url: str = "",
    pinned_sha: str = "",
) -> InstallResult:
    preview.origin = origin
    preview.source_url = source_url
    preview.pinned_sha = pinned_sha
    if preview.errors:
        return InstallResult(
            ok=False,
            dry_run=dry_run,
            preview=preview,
            detail="; ".join(preview.errors),
        )

    pending_body = {
        "markdown": markdown,
        "name": preview.name,
        "origin": origin,
        "source_url": source_url,
        "pinned_sha": pinned_sha,
        "preview": asdict(preview),
    }
    if dry_run:
        install_id = _write_pending(pending_body)
        return InstallResult(
            ok=True,
            dry_run=True,
            install_id=install_id,
            skill_id=preview.name,
            preview=preview,
            detail="预览通过，确认后写入",
        )

    _materialize(
        preview.name,
        markdown,
        {
            "origin": origin,
            "source_url": source_url,
            "pinned_sha": pinned_sha,
            "installed_at": time.time(),
            "content_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        },
    )
    _append_ledger(
        {
            "skill_id": preview.name,
            "origin": origin,
            "source_url": source_url,
            "pinned_sha": pinned_sha,
            "verdict": "installed",
            "at": time.time(),
            "warnings": preview.warnings,
        }
    )
    return InstallResult(
        ok=True,
        dry_run=False,
        skill_id=preview.name,
        preview=preview,
        installed=True,
        detail="已安装",
    )


def install_from_paste(
    markdown: str,
    *,
    name: str | None = None,
    dry_run: bool = True,
) -> InstallResult:
    preview = review_skill_markdown(markdown, name_hint=name)
    return _finalize(
        markdown=markdown.replace("\r\n", "\n"),
        preview=preview,
        dry_run=dry_run,
        origin="local",
    )


def install_from_zip(
    data: bytes,
    *,
    dry_run: bool = True,
) -> InstallResult:
    if len(data) > MAX_ZIP_BYTES:
        prev = SkillPreview(name="invalid")
        prev.errors.append(f"zip 超过 {MAX_ZIP_BYTES} 字节上限")
        return InstallResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        prev = SkillPreview(name="invalid")
        prev.errors.append("无效的 zip 文件")
        return InstallResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])

    skill_members = [
        n
        for n in zf.namelist()
        if n.lower().endswith("skill.md") and not n.endswith("/") and ".." not in n
    ]
    if not skill_members:
        prev = SkillPreview(name="invalid")
        prev.errors.append("zip 内未找到 SKILL.md")
        return InstallResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])
    # Prefer shallowest SKILL.md
    skill_members.sort(key=lambda n: (n.count("/"), len(n)))
    member = skill_members[0]
    if any(part.startswith("/") or part == ".." for part in Path(member).parts):
        prev = SkillPreview(name="invalid")
        prev.errors.append("zip 路径非法")
        return InstallResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])

    total = 0
    count = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        count += 1
        if count > MAX_FILES:
            prev = SkillPreview(name="invalid")
            prev.errors.append(f"zip 文件数超过 {MAX_FILES}")
            return InstallResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])
        total += info.file_size
        if total > MAX_TOTAL_TEXT_BYTES:
            prev = SkillPreview(name="invalid")
            prev.errors.append("zip 解压文本总量超限")
            return InstallResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])
        # Reject obvious binaries by extension
        lower = info.filename.lower()
        if lower.endswith((".exe", ".dll", ".so", ".dylib", ".bin")):
            prev = SkillPreview(name="invalid")
            prev.errors.append(f"拒绝可执行文件: {info.filename}")
            return InstallResult(ok=False, dry_run=dry_run, preview=prev, detail=prev.errors[0])

    markdown = zf.read(member).decode("utf-8")
    # name hint from parent folder
    parent = Path(member).parent.name
    name_hint = parent if parent and parent not in {".", ""} else None
    preview = review_skill_markdown(markdown, name_hint=name_hint)
    preview.file_count = count
    return _finalize(markdown=markdown, preview=preview, dry_run=dry_run, origin="local")


def install_from_github(
    url: str,
    *,
    ref: str | None = None,
    path: str | None = None,
    dry_run: bool = True,
    fetch: FetchFn | None = None,
) -> InstallResult:
    try:
        loc = parse_github_skill_url(url)
    except ValueError as exc:
        prev = SkillPreview(name="invalid")
        prev.errors.append(str(exc))
        return InstallResult(ok=False, dry_run=dry_run, preview=prev, detail=str(exc))
    if ref:
        loc["ref"] = ref
    if path is not None and str(path).strip() != "":
        loc["path"] = str(path).strip().strip("/")
    try:
        markdown, pinned = fetch_github_skill_markdown(loc, fetch=fetch)
    except Exception as exc:  # noqa: BLE001
        prev = SkillPreview(name="invalid")
        prev.errors.append(f"拉取失败: {exc}")
        return InstallResult(ok=False, dry_run=dry_run, preview=prev, detail=str(exc))

    source = f"https://github.com/{loc['owner']}/{loc['repo']}/tree/{loc['ref']}"
    if loc["path"]:
        source = f"{source}/{loc['path']}"
    preview = review_skill_markdown(markdown)
    return _finalize(
        markdown=markdown,
        preview=preview,
        dry_run=dry_run,
        origin="github",
        source_url=source,
        pinned_sha=pinned,
    )


def confirm_install(install_id: str) -> InstallResult:
    try:
        payload = _load_pending(install_id)
    except FileNotFoundError as exc:
        return InstallResult(ok=False, dry_run=False, detail=str(exc))

    markdown = str(payload.get("markdown") or "")
    preview = review_skill_markdown(markdown, name_hint=str(payload.get("name") or None))
    # Re-run review in case whitelist changed
    if preview.errors:
        return InstallResult(
            ok=False,
            dry_run=False,
            install_id=install_id,
            preview=preview,
            detail="; ".join(preview.errors),
        )
    origin = str(payload.get("origin") or "local")
    source_url = str(payload.get("source_url") or "")
    pinned_sha = str(payload.get("pinned_sha") or "")
    result = _finalize(
        markdown=markdown,
        preview=preview,
        dry_run=False,
        origin=origin,
        source_url=source_url,
        pinned_sha=pinned_sha,
    )
    if result.installed:
        _clear_pending(install_id)
        result.install_id = install_id
    return result


def uninstall_skill(skill_id: str) -> dict[str, Any]:
    name = (skill_id or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("非法 skill id")
    if name in _bundled_skill_ids():
        raise ValueError("不能卸载内置技能")
    dest = skills_dir() / name
    if not dest.is_dir():
        raise FileNotFoundError(f"未安装: {name}")
    shutil.rmtree(dest)
    _append_ledger({"skill_id": name, "verdict": "uninstalled", "at": time.time()})
    return {"ok": True, "skill_id": name}


def list_installed() -> list[dict[str, Any]]:
    root = skills_dir()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for skill_md in sorted(root.glob("*/SKILL.md")):
        name = skill_md.parent.name
        meta: dict[str, Any] = {}
        mp = install_meta_path(name)
        if mp.is_file():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {}
        fields, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        out.append(
            {
                "id": fields.get("name") or name,
                "origin": meta.get("origin") or "local",
                "source_url": meta.get("source_url") or "",
                "pinned_sha": meta.get("pinned_sha") or "",
                "installed_at": meta.get("installed_at"),
                "path": str(skill_md),
            }
        )
    return out


def result_to_dict(result: InstallResult) -> dict[str, Any]:
    d: dict[str, Any] = {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "install_id": result.install_id,
        "skill_id": result.skill_id,
        "installed": result.installed,
        "detail": result.detail,
    }
    if result.preview:
        d["preview"] = asdict(result.preview)
    return d
