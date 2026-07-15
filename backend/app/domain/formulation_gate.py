"""Validate and enrich Formulation / RecommendedFormula objects."""
from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from .knowledge import RAW_MATERIALS
from .schemas import (
    Formulation,
    Ingredient,
    ProductDomain,
    RecommendedFormula,
    RecommendedFormulaComponent,
    RecommendedFormulaListResponse,
)


class FormulationListResponse(BaseModel):
    """Legacy LLM structured output shape."""

    formulations: list[Formulation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _chemtools_gap_fill(name: str, has_smiles: bool, has_cas: bool) -> dict:
    """Resolve missing SMILES/CAS via the ChemCrow gateway (cached; no-op offline)."""
    if has_smiles and has_cas:
        return {}
    from ..services import chemtools

    if not (chemtools.gateway_enabled() and chemtools.chemcrow_available()):
        return {}
    updates: dict = {}
    if not has_smiles:
        smiles = chemtools.name_to_smiles(name)
        if smiles:
            updates["smiles"] = smiles
    if not has_cas:
        cas = chemtools.name_to_cas(name)
        if cas:
            updates["cas_no"] = cas
    return updates


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
    if not ing.component_type and (ing.role or spec.get("role")):
        updates["component_type"] = ing.role or spec.get("role", "")
    updates.update(
        _chemtools_gap_fill(
            ing.name,
            has_smiles=bool(ing.smiles or updates.get("smiles")),
            has_cas=bool(ing.cas_no or updates.get("cas_no")),
        )
    )
    return ing.model_copy(update=updates) if updates else ing


def enrich_component(comp: RecommendedFormulaComponent) -> RecommendedFormulaComponent:
    spec = RAW_MATERIALS.get(comp.name, {})
    updates: dict = {}
    if not comp.cas_no and spec.get("cas_no"):
        updates["cas_no"] = spec["cas_no"]
    if not comp.smiles and spec.get("smiles"):
        updates["smiles"] = spec["smiles"]
    if not comp.mf and spec.get("formula"):
        updates["mf"] = spec["formula"]
    if comp.molar_mass is None and spec.get("molar_mass") is not None:
        updates["molar_mass"] = spec["molar_mass"]
    if not comp.component_type and spec.get("role"):
        updates["component_type"] = spec["role"]
    updates.update(
        _chemtools_gap_fill(
            comp.name,
            has_smiles=bool(comp.smiles or updates.get("smiles")),
            has_cas=bool(comp.cas_no or updates.get("cas_no")),
        )
    )
    return comp.model_copy(update=updates) if updates else comp


def enrich_formulation(form: Formulation) -> Formulation:
    ingredients = [enrich_ingredient(i) for i in form.ingredients]
    return form.model_copy(update={"ingredients": ingredients})


def component_to_ingredient(comp: RecommendedFormulaComponent) -> Ingredient:
    enriched = enrich_component(comp)
    weight = enriched.weight_pct
    if weight is None:
        weight = 0.0
    role = enriched.component_type or "additive"
    return Ingredient(
        name=enriched.name,
        zh_name=enriched.zh_name or "",
        role=role,
        component_type=enriched.component_type or role,
        smiles=enriched.smiles,
        formula=enriched.mf or None,
        mf_structure=enriched.mf or None,
        cas_no=enriched.cas_no or None,
        molar_mass=enriched.molar_mass,
        weight_pct=float(weight),
        equivalents=enriched.equivalents,
        mmol=enriched.mmol,
        amount_display=enriched.amount_display or (f"{weight:.2f}%" if weight else ""),
        notes=enriched.notes,
        evidence_refs=list(enriched.evidence_refs or []),
        grounding_confidence=enriched.grounding_confidence,
    )


def ingredient_to_component(ing: Ingredient) -> RecommendedFormulaComponent:
    enriched = enrich_ingredient(ing)
    return RecommendedFormulaComponent(
        component_type=enriched.component_type or enriched.role,
        name=enriched.name,
        zh_name=enriched.zh_name or "",
        cas_no=enriched.cas_no or "",
        mf=enriched.formula or enriched.mf_structure or "",
        smiles=enriched.smiles,
        molar_mass=enriched.molar_mass,
        equivalents=enriched.equivalents,
        mmol=enriched.mmol,
        amount_display=enriched.amount_display or f"{enriched.weight_pct:.2f}%",
        weight_pct=enriched.weight_pct,
        notes=enriched.notes,
        evidence_refs=list(enriched.evidence_refs or []),
        grounding_confidence=enriched.grounding_confidence,
    )


def recommended_to_formulation(rec: RecommendedFormula) -> Formulation:
    ingredients = [component_to_ingredient(c) for c in rec.components]
    if not ingredients:
        raise ValueError(f"RecommendedFormula {rec.name!r} has no components")
    total = sum(i.weight_pct for i in ingredients)
    if total <= 0 and len(ingredients) > 0:
        share = 100.0 / len(ingredients)
        ingredients = [i.model_copy(update={"weight_pct": share}) for i in ingredients]
    return Formulation(
        name=rec.name,
        domain=rec.domain,
        ingredients=ingredients,
        rationale=rec.rationale,
        predicted=dict(rec.predicted),
        score=rec.score,
        warnings=list(rec.warnings),
    )


def formulation_to_recommended(
    form: Formulation,
    *,
    engine: str = "offline",
    objectives_summary: str = "",
) -> RecommendedFormula:
    enriched = enrich_formulation(form)
    return RecommendedFormula(
        name=enriched.name,
        domain=enriched.domain,
        rationale=enriched.rationale,
        objectives_summary=objectives_summary,
        components=[ingredient_to_component(i) for i in enriched.ingredients],
        predicted=dict(enriched.predicted),
        score=enriched.score,
        warnings=list(enriched.warnings),
        engine="llm" if engine == "llm" else "offline",
    )


def formulations_to_recommended(
    forms: list[Formulation],
    *,
    engine: str = "offline",
) -> list[RecommendedFormula]:
    return [formulation_to_recommended(f, engine=engine) for f in forms]


def validate_formulations(forms: list[Formulation]) -> tuple[list[Formulation], list[str]]:
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


def validate_recommended_formulas(
    formulas: list[RecommendedFormula],
) -> tuple[list[RecommendedFormula], list[str]]:
    warnings: list[str] = []
    out: list[RecommendedFormula] = []
    for rec in formulas:
        if not rec.components:
            warnings.append(f"{rec.name}: no components; skipped")
            continue
        enriched_comps = [enrich_component(c) for c in rec.components]
        missing_cas = [c.name for c in enriched_comps if not c.cas_no]
        if missing_cas:
            warnings.append(f"{rec.name}: missing CAS for {', '.join(missing_cas[:3])}")
        missing_mf = [c.name for c in enriched_comps if not c.mf]
        if missing_mf:
            warnings.append(f"{rec.name}: missing MF for {', '.join(missing_mf[:3])}")
        weights = [c.weight_pct for c in enriched_comps if c.weight_pct is not None]
        if weights and abs(sum(weights) - 100.0) > 8.0:
            warnings.append(f"{rec.name}: weight_pct sum {sum(weights):.1f}% (expected ~100%)")
        out.append(rec.model_copy(update={"components": enriched_comps}))
    return out, warnings


def parse_llm_formulations(payload: dict | list) -> tuple[list[Formulation], list[str]]:
    try:
        if isinstance(payload, list):
            forms = [Formulation(**item) if isinstance(item, dict) else item for item in payload]
            parsed = FormulationListResponse(formulations=forms)
        else:
            parsed = FormulationListResponse.model_validate(payload)
        return validate_formulations(parsed.formulations)
    except ValidationError as exc:
        return [], [f"LLM formulation JSON invalid: {exc}"]


def parse_llm_recommended(payload: dict) -> tuple[RecommendedFormulaListResponse | None, str | None]:
    try:
        parsed = RecommendedFormulaListResponse.model_validate(payload)
        formulas, warnings = validate_recommended_formulas(parsed.formulas)
        return parsed.model_copy(update={"formulas": formulas, "warnings": warnings + parsed.warnings}), None
    except ValidationError as exc:
        return None, str(exc)


def offline_recommend_response(
    forms: list[Formulation],
    *,
    reason: str,
) -> RecommendedFormulaListResponse:
    recs = formulations_to_recommended(forms, engine="offline")
    return RecommendedFormulaListResponse(
        formulas=recs,
        warnings=[reason],
        engine="offline",
    )
