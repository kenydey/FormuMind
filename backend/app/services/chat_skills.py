"""Chat skill packs (SKILL.md) — discovery, parse, inject."""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n?", re.MULTILINE)

ALLOWED_CHAT_TOOLS = frozenset(
    {
        "kb_hybrid",
        "literature_search",
        "evidence_synthesis",
        "scholar_doi",
        "wiki_draft",
        "chemistry_pubchem",
    }
)


def parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(raw.replace("\r\n", "\n"))
    if not match:
        return {}, raw
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip().strip("'\"")
        if key:
            fields[key] = val
    body = raw[match.end() :].lstrip("\n")
    return fields, body


def _origin_for_user_skill(skill_md: Path, default: str) -> str:
    meta = skill_md.parent / ".formumind-install.json"
    if meta.is_file():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            origin = str(data.get("origin") or "").strip()
            if origin in {"github", "local", "user", "pack"}:
                return "local" if origin == "user" else origin
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return default


def _load_skill_dir(root: Path, *, origin: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for skill_md in sorted(root.glob("*/SKILL.md")):
        try:
            raw = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("skill read failed %s: %s", skill_md, exc)
            continue
        fields, body = parse_frontmatter(raw)
        name = fields.get("name") or skill_md.parent.name
        tools_raw = fields.get("allowed_tools") or ""
        tools = [t.strip() for t in re.split(r"[\s,]+", tools_raw) if t.strip()]
        resolved_origin = origin if origin == "bundled" else _origin_for_user_skill(skill_md, origin)
        out.append(
            {
                "id": name,
                "kind": "chat_skill",
                "title": fields.get("summary") or fields.get("description") or name,
                "summary": fields.get("summary") or fields.get("description") or "",
                "description": fields.get("description") or "",
                "when_to_use": fields.get("description") or "",
                "category": fields.get("category") or "research",
                "activation_policy": fields.get("activation_policy") or "user-controlled",
                "allowed_tools": validate_allowed_tools(tools),
                "entry": (fields.get("entry") or "true").lower() != "false",
                "origin": resolved_origin,
                "location": str(skill_md),
                "body": body,
                "icon": "📘",
                "action": "chat",
                "modal": None,
                "tools": validate_allowed_tools(tools),
                "checklist": [],
                "presets": {},
            }
        )
    return out


def validate_allowed_tools(tools: list[str]) -> list[str]:
    return [t for t in tools if t in ALLOWED_CHAT_TOOLS]


# P-3: process-wide cache for the parsed skill list. The chat prompt path
# calls list_chat_skills on every message; re-reading + parsing every
# SKILL.md each time is pure waste. Invalidated by filesystem mtime:
# directory mtimes catch install/uninstall/rename, file mtimes catch
# in-place edits and .formumind-install.json (origin) changes.
# Callers get shallow copies, so popping "body" never corrupts the cache.
_SKILLS_CACHE: dict[str, Any] = {"key": None, "rows": None}
_SKILLS_CACHE_LOCK = threading.Lock()


def _skill_roots() -> list[Path]:
    return [
        Path(__file__).resolve().parents[1] / "resources" / "chat_skills",
        Path("./data").resolve() / "skills",
    ]


def _skills_cache_key() -> tuple | None:
    """Fingerprint of (path, mtime_ns) for dirs + SKILL.md + install meta.

    Returns None when the fingerprint itself cannot be built (fail-safe:
    bypass the cache and re-read from disk).
    """
    try:
        stamps: list[tuple[str, int]] = []
        for root in _skill_roots():
            try:
                is_dir = root.is_dir()
            except OSError:
                continue
            if not is_dir:
                stamps.append((str(root), -1))
                continue
            try:
                stamps.append((str(root), root.stat().st_mtime_ns))
            except OSError:
                stamps.append((str(root), -1))
            for pattern in ("*/SKILL.md", "*/.formumind-install.json"):
                try:
                    files = sorted(root.glob(pattern))
                except OSError:
                    continue
                for f in files:
                    try:
                        stamps.append((str(f), f.stat().st_mtime_ns))
                    except OSError:
                        continue
        return tuple(stamps)
    except Exception:  # noqa: BLE001 — fail-safe: no caching
        return None


def invalidate_chat_skills_cache() -> None:
    """Explicit invalidation (e.g. after skill install/uninstall flows)."""
    with _SKILLS_CACHE_LOCK:
        _SKILLS_CACHE["key"] = None
        _SKILLS_CACHE["rows"] = None


def _load_all_chat_skills() -> list[dict[str, Any]]:
    """Full parse (with body), sorted — the cacheable superset."""
    by_id: dict[str, dict[str, Any]] = {}
    for root, origin in (
        (_skill_roots()[0], "bundled"),
        (_skill_roots()[1], "local"),
    ):
        if not root.is_dir():
            continue
        for skill in _load_skill_dir(root, origin=origin):
            by_id[skill["id"]] = skill
    return sorted(by_id.values(), key=lambda r: r["id"])


def list_chat_skills(*, include_body: bool = False) -> list[dict[str, Any]]:
    key = _skills_cache_key()
    rows: list[dict[str, Any]] | None = None
    if key is not None:
        with _SKILLS_CACHE_LOCK:
            if _SKILLS_CACHE["key"] == key and _SKILLS_CACHE["rows"] is not None:
                rows = _SKILLS_CACHE["rows"]
    if rows is None:
        rows = _load_all_chat_skills()
        if key is not None:
            with _SKILLS_CACHE_LOCK:
                _SKILLS_CACHE["key"] = key
                _SKILLS_CACHE["rows"] = rows
    if include_body:
        return [dict(r) for r in rows]
    return [{k: v for k, v in r.items() if k != "body"} for r in rows]


def get_chat_skill(skill_id: str, *, include_body: bool = True) -> dict[str, Any] | None:
    for skill in list_chat_skills(include_body=True):
        if skill["id"] == skill_id:
            if not include_body:
                return {k: v for k, v in skill.items() if k != "body"}
            return skill
    return None


def skill_prompt_block(skill_ids: list[str]) -> str:
    """Concatenate bodies of selected (and enabled) chat skills for system inject."""
    from .skills_store import is_enabled

    parts: list[str] = []
    for sid in skill_ids:
        skill = get_chat_skill(sid, include_body=True)
        if not skill:
            continue
        policy = skill.get("activation_policy") or "user-controlled"
        if not is_enabled(sid, activation_policy=policy):
            continue
        body = (skill.get("body") or "").strip()
        if not body:
            continue
        parts.append(f"### Skill: {skill['id']}\n{body}")
    if not parts:
        return ""
    return "# Active chat skills\n\n" + "\n\n".join(parts)
