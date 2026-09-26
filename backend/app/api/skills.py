"""Unified Skills API — playbooks + chat skills + preferences."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..resources.formulation_skills import get_formulation_skill, list_formulation_skills
from ..services import chat_skills as chat_skills_svc
from ..services.skills_store import is_enabled, load_prefs, save_prefs

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillOut(BaseModel):
    id: str
    kind: str
    title: str
    summary: str = ""
    when_to_use: str = ""
    description: str = ""
    action: str = "chat"
    modal: str | None = None
    icon: str = "✦"
    tools: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    checklist: list[dict] = Field(default_factory=list)
    presets: dict = Field(default_factory=dict)
    activation_policy: str = "user-controlled"
    origin: str = "bundled"
    category: str = ""
    enabled: bool = True
    pinned: bool = False
    entry: bool = True


class SkillsPrefsPatch(BaseModel):
    disabled_ids: list[str] | None = None
    pinned_ids: list[str] | None = None
    evidence_mode_default: bool | None = None


class SkillsCatalogResponse(BaseModel):
    skills: list[SkillOut]
    prefs: dict


def _playbook_rows() -> list[dict]:
    rows = []
    for s in list_formulation_skills():
        row = dict(s)
        row.setdefault("kind", "playbook")
        row.setdefault("activation_policy", "user-controlled")
        row.setdefault("origin", "bundled")
        row.setdefault("description", row.get("summary") or "")
        row.setdefault("allowed_tools", row.get("tools") or [])
        row.setdefault("category", "formulation")
        row.setdefault("entry", True)
        rows.append(row)
    return rows


def _annotate(rows: list[dict]) -> list[SkillOut]:
    prefs = load_prefs()
    pinned = set(prefs.get("pinned_ids") or [])
    out: list[SkillOut] = []
    for r in rows:
        policy = r.get("activation_policy") or "user-controlled"
        enabled = is_enabled(r["id"], activation_policy=policy)
        out.append(
            SkillOut(
                id=r["id"],
                kind=r.get("kind") or "chat_skill",
                title=r.get("title") or r["id"],
                summary=r.get("summary") or "",
                when_to_use=r.get("when_to_use") or "",
                description=r.get("description") or r.get("summary") or "",
                action=r.get("action") or "chat",
                modal=r.get("modal"),
                icon=r.get("icon") or "✦",
                tools=list(r.get("tools") or []),
                allowed_tools=list(r.get("allowed_tools") or r.get("tools") or []),
                checklist=list(r.get("checklist") or []),
                presets=dict(r.get("presets") or {}),
                activation_policy=policy,
                origin=r.get("origin") or "bundled",
                category=r.get("category") or "",
                enabled=enabled,
                pinned=r["id"] in pinned,
                entry=bool(r.get("entry", True)),
            )
        )
    # pinned first, then kind, then id
    out.sort(key=lambda s: (not s.pinned, s.kind, s.id))
    return out


@router.get("", response_model=SkillsCatalogResponse)
def catalog(kind: str | None = None) -> SkillsCatalogResponse:
    rows = _playbook_rows() + chat_skills_svc.list_chat_skills(include_body=False)
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    return SkillsCatalogResponse(skills=_annotate(rows), prefs=load_prefs())


@router.post("/prefs")
def patch_prefs(body: SkillsPrefsPatch) -> dict:
    prefs = save_prefs(body.model_dump(exclude_none=True))
    return {"prefs": prefs, "skills": _annotate(_playbook_rows() + chat_skills_svc.list_chat_skills())}


@router.get("/{skill_id}", response_model=SkillOut)
def get_skill(skill_id: str) -> SkillOut:
    pb = get_formulation_skill(skill_id)
    if pb:
        row = dict(pb)
        row.setdefault("kind", "playbook")
        row.setdefault("activation_policy", "user-controlled")
        row.setdefault("origin", "bundled")
        return _annotate([row])[0]
    cs = chat_skills_svc.get_chat_skill(skill_id, include_body=False)
    if cs:
        return _annotate([cs])[0]
    raise HTTPException(status_code=404, detail="skill not found")
