"""Metadata endpoint: domains, substrates, DOE designs, and baseline templates."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..domain.examples import BUILTIN_METRICS, EXAMPLE_PROJECTS, ROLE_CATALOG, load_example
from ..domain.formulation_gate import recommended_to_formulation, validate_formulations
from ..domain.knowledge import RAW_MATERIALS, baseline_formulation
from ..domain.objective_contract import normalize_objectives
from ..domain.schemas import (
    Evidence,
    Formulation,
    ObjectiveSpec,
    ProductDomain,
    RecommendedFormula,
    Requirement,
    Substrate,
)
from ..pipeline import workflow
from ..services import llm

router = APIRouter(prefix="/api", tags=["metadata"])
log = logging.getLogger(__name__)

from ..services.engines.pydoe_engine import PYDOE_DESIGNS

_NATIVE_DESIGNS = ["full_factorial", "fractional_factorial", "plackett_burman", "ccd", "lhs"]
_ALL_DESIGNS = _NATIVE_DESIGNS + [d for d in PYDOE_DESIGNS if d not in _NATIVE_DESIGNS]


@router.get("/meta")
def metadata() -> dict:
    return {
        "domains": [d.value for d in ProductDomain],
        "substrates": [s.value for s in Substrate],
        "designs": _ALL_DESIGNS,
        "doe_engines": ["auto", "native", "pydoe"],
        "al_engines": ["auto", "legacy", "baybe"],
        "pydoe_designs": list(PYDOE_DESIGNS),
        "example_projects": [
            {"id": k, "label": v["label"], "domain": v["domain"].value}
            for k, v in EXAMPLE_PROJECTS.items()
        ],
        "builtin_metrics": BUILTIN_METRICS,
        "role_catalog": ROLE_CATALOG,
    }


@router.get("/examples/{example_id}", response_model=Requirement)
def get_example_project(example_id: str) -> Requirement:
    return load_example(example_id)


@router.get("/ingredients")
def ingredients() -> dict:
    """Return the full raw-material library including price and VOC metadata."""
    return {
        name: {
            "role": spec.get("role"),
            "formula": spec.get("formula"),
            "cas_no": spec.get("cas_no"),
            "molar_mass": spec.get("molar_mass"),
            "price_cny_per_kg": spec.get("price_cny_per_kg"),
            "voc_contrib": spec.get("voc_contrib"),
        }
        for name, spec in RAW_MATERIALS.items()
    }


@router.get("/templates/{domain}", response_model=Formulation)
def template(domain: ProductDomain) -> Formulation:
    req = Requirement(domain=domain)
    return baseline_formulation(req)


class FormulationValidateRequest(BaseModel):
    formulations: list[Formulation]


class FormulationValidateResponse(BaseModel):
    formulations: list[Formulation]
    warnings: list[str]


@router.post("/formulations/validate", response_model=FormulationValidateResponse)
def validate_formulation_list(body: FormulationValidateRequest) -> FormulationValidateResponse:
    """Validate and enrich leaderboard / LLM formulations (CAS, structure)."""
    forms, warnings = validate_formulations(body.formulations)
    return FormulationValidateResponse(formulations=forms, warnings=warnings)


class RecommendFormulationsRequest(BaseModel):
    requirement: Requirement
    objectives: list[ObjectiveSpec] = Field(default_factory=list)
    sources: list[Evidence] = Field(default_factory=list)
    n: int = Field(default=3, ge=1, le=8)


class RecommendFormulationsResponse(BaseModel):
    formulas: list[RecommendedFormula]
    engine: str
    warnings: list[str] = Field(default_factory=list)
    scored: list[Formulation] = Field(default_factory=list)


@router.post("/formulations/recommend", response_model=RecommendFormulationsResponse)
def recommend_formulations(body: RecommendFormulationsRequest) -> RecommendFormulationsResponse:
    """LLM structured formulation recommend grounded on ColBERT + CRAG evidence."""
    from ..pipeline.research_graph import resolve_grounded_evidence

    objectives = body.objectives or normalize_objectives(body.requirement)
    query = body.requirement.headline()
    try:
        grounded_result = resolve_grounded_evidence(
            body.requirement,
            query,
            pre_index=body.sources or None,
        )
        evidence = grounded_result.grounded_evidence
        rec_resp = llm.recommend_formulations(
            body.requirement,
            objectives,
            evidence,
            n=body.n,
        )
    except Exception as exc:
        log.exception("recommend_formulations failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not rec_resp.formulas:
        raise HTTPException(status_code=503, detail="No formulations produced")

    process = workflow.process_for(body.requirement)
    scored: list[Formulation] = []
    for rec in rec_resp.formulas:
        try:
            form = recommended_to_formulation(rec)
            scored.append(workflow._score_and_validate(form, process, body.requirement))
        except Exception as exc:
            log.warning("Skip scoring formula %s: %s", rec.name, exc)
            rec_resp.warnings.append(f"Scoring skipped for {rec.name}: {exc}")

    scored.sort(key=lambda f: (f.score or 0.0), reverse=True)
    return RecommendFormulationsResponse(
        formulas=rec_resp.formulas,
        engine=rec_resp.engine,
        warnings=rec_resp.warnings,
        scored=scored,
    )


class ManualFormulationRequest(BaseModel):
    formulation: Formulation
    requirement: Requirement | None = None


class ManualFormulationResponse(BaseModel):
    formulation: Formulation
    warnings: list[str] = Field(default_factory=list)


@router.post("/formulations/manual", response_model=ManualFormulationResponse)
def add_manual_formulation(body: ManualFormulationRequest) -> ManualFormulationResponse:
    """Validate, enrich, and optionally score a manually entered formulation."""
    forms, warnings = validate_formulations([body.formulation])
    form = forms[0].model_copy(update={"source": "manual"})
    if body.requirement:
        process = workflow.process_for(body.requirement)
        form = workflow._score_and_validate(form, process, body.requirement)
    return ManualFormulationResponse(formulation=form, warnings=warnings)
