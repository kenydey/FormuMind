"""Chat skill packs (SKILL.md) — discovery, parse, inject."""
from __future__ import annotations

import logging
import re
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


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
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


def _load_skill_dir(root: Path, *, origin: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for skill_md in sorted(root.glob("*/SKILL.md")):
        try:
            raw = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("skill read failed %s: %s", skill_md, exc)
            continue
        fields, body = _parse_frontmatter(raw)
        name = fields.get("name") or skill_md.parent.name
        tools_raw = fields.get("allowed_tools") or ""
        tools = [t.strip() for t in re.split(r"[\s,]+", tools_raw) if t.strip()]
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
                "origin": origin,
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


def list_chat_skills(*, include_body: bool = False) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    pairs = (
        (Path(__file__).resolve().parents[1] / "resources" / "chat_skills", "bundled"),
        (Path("./data").resolve() / "skills", "user"),
    )
    for root, origin in pairs:
        if not root.is_dir():
            continue
        for skill in _load_skill_dir(root, origin=origin):
            by_id[skill["id"]] = skill
    rows = list(by_id.values())
    if not include_body:
        for r in rows:
            r.pop("body", None)
    return sorted(rows, key=lambda r: r["id"])


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
