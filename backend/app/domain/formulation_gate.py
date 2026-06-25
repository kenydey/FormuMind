"""Validate and enrich Formulation objects from knowledge or LLM JSON."""
from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from .knowledge import RAW_MATERIALS
from .schemas import Formulation, Ingredient


class FormulationListResponse(BaseModel):
    """Expected LLM structured output for recommended formulations."""

    formulations: list[Formulation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def enrich_ingredient(ing: Ingredient) -> Ingredient:
    spec = RAW_MATERIALS.get(ing.name, {})
    updates: dict = {}
    if not ing.cas_no and spec.get("cas_no"):
        updates["cas_no"] = spec["cas_no"]
    if not ing.smiles and spec.get("smiles"):
        updates["smiles"] = spec["smiles"]
    if not ing.formula and spec.get("formula"):
        updates["formula"] = spec["formula"]
        updates["mf_structure"] = spec["formula"]
    if ing.molar_mass is None and spec.get("molar_mass") is not None:
        updates["molar_mass"] = spec["molar_mass"]
    if not ing.role and spec.get("role"):
        updates["role"] = spec["role"]
    return ing.model_copy(update=updates) if updates else ing


def enrich_formulation(form: Formulation) -> Formulation:
    ingredients = [enrich_ingredient(i) for i in form.ingredients]
    return form.model_copy(update={"ingredients": ingredients})


def validate_formulations(forms: list[Formulation]) -> tuple[list[Formulation], list[str]]:
    """Return enriched formulations and non-fatal validation warnings."""
    warnings: list[str] = []
    out: list[Formulation] = []
    for form in forms:
        if not form.ingredients:
            warnings.append(f"Formulation {form.name!r} has no ingredients; skipped")
            continue
        total_wt = sum(i.weight_pct for i in form.ingredients)
        if abs(total_wt - 100.0) > 5.0:
            warnings.append(f"{form.name}: ingredient weights sum to {total_wt:.1f}% (expected ~100%)")
        missing_cas = [i.name for i in form.ingredients if not enrich_ingredient(i).cas_no]
        if missing_cas and len(missing_cas) == len(form.ingredients):
            warnings.append(f"{form.name}: no CAS numbers resolved for ingredients")
        out.append(enrich_formulation(form))
    return out, warnings


def parse_llm_formulations(payload: dict | list) -> tuple[list[Formulation], list[str]]:
    """Parse LLM JSON into formulations; return empty list + errors on failure."""
    try:
        if isinstance(payload, list):
            forms = [Formulation(**item) if isinstance(item, dict) else item for item in payload]
            parsed = FormulationListResponse(formulations=forms)
        else:
            parsed = FormulationListResponse.model_validate(payload)
        return validate_formulations(parsed.formulations)
    except ValidationError as exc:
        return [], [f"LLM formulation JSON invalid: {exc}"]
