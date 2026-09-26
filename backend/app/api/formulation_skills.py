"""Read-only formulation action skills (Dim-5)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..resources.formulation_skills import get_formulation_skill, list_formulation_skills

router = APIRouter(prefix="/api/formulation-skills", tags=["formulation-skills"])


class SkillChecklistItem(BaseModel):
    id: str
    title: str


class FormulationSkillOut(BaseModel):
    id: str
    kind: str = "playbook"
    title: str
    summary: str
    when_to_use: str = ""
    action: str
    modal: str | None = None
    icon: str = "✦"
    tools: list[str] = Field(default_factory=list)
    checklist: list[SkillChecklistItem] = Field(default_factory=list)
    presets: dict = Field(default_factory=dict)


@router.get("", response_model=list[FormulationSkillOut])
def list_skills() -> list[FormulationSkillOut]:
    return [FormulationSkillOut.model_validate(s) for s in list_formulation_skills()]


@router.get("/{skill_id}", response_model=FormulationSkillOut)
def get_skill(skill_id: str) -> FormulationSkillOut:
    skill = get_formulation_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return FormulationSkillOut.model_validate(skill)
