"""Metadata endpoint: domains, substrates, DOE designs, and baseline templates."""
from __future__ import annotations

from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
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
from ..domain.tradeoff_schemas import TradeOffAnalysis
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
    requirement: Requirement | None = None


class FormulationValidateResponse(BaseModel):
    formulations: list[Formulation]
    warnings: list[str]


@router.post("/formulations/validate", response_model=FormulationValidateResponse)
def validate_formulation_list(body: FormulationValidateRequest) -> FormulationValidateResponse:
    """Validate and enrich leaderboard / LLM formulations (CAS, structure)."""
    forms, warnings = validate_formulations(body.formulations, req=body.requirement)
    return FormulationValidateResponse(formulations=forms, warnings=warnings)


class RecommendFormulationsRequest(BaseModel):
    requirement: Requirement
    objectives: list[ObjectiveSpec] = Field(default_factory=list)
    sources: list[Evidence] = Field(default_factory=list)
    n: int | None = Field(default=None, ge=1, le=12)
    include_tradeoff: bool = True
    scenario_kinds: list[str] = Field(default_factory=list)


class RecommendFormulationsResponse(BaseModel):
    formulas: list[RecommendedFormula]
    engine: str
    warnings: list[str] = Field(default_factory=list)
    scored: list[Formulation] = Field(default_factory=list)
    requested_n: int | None = None
    returned_n: int | None = None
    diversity_applied: bool = False
    tradeoff: TradeOffAnalysis | None = None


@router.post("/formulations/recommend", response_model=RecommendFormulationsResponse)
def recommend_formulations(body: RecommendFormulationsRequest) -> RecommendFormulationsResponse:
    """LLM structured formulation recommend grounded on ColBERT + CRAG evidence."""
    from ..config import get_settings
    from ..pipeline.research_graph import resolve_grounded_evidence
    from ..services.grounded_recommend import ground_recommended_formulas
    from ..services.recommend_pipeline import finalize_recommendation_bundle, llm_candidate_count, resolve_recommend_n

    settings = get_settings()
    objectives = body.objectives or normalize_objectives(body.requirement)
    requested_n = resolve_recommend_n(body.n, settings=settings)
    llm_n = llm_candidate_count(requested_n, settings=settings)
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
            n=llm_n,
        )
    except Exception as exc:
        log.exception("recommend_formulations failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not rec_resp.formulas:
        raise HTTPException(status_code=503, detail="No formulations produced")

    grounded_formulas, ground_warnings = ground_recommended_formulas(rec_resp.formulas, evidence)
    rec_resp.warnings.extend(ground_warnings)

    aligned, scored, extra_warnings, _, diversity_applied, tradeoff = finalize_recommendation_bundle(
        grounded_formulas,
        body.requirement,
        evidence,
        requested_n=requested_n,
        objectives=objectives,
        include_tradeoff=body.include_tradeoff,
        scenario_kinds=body.scenario_kinds or None,
        settings=settings,
    )
    rec_resp.warnings.extend(extra_warnings)

    return RecommendFormulationsResponse(
        formulas=aligned,
        engine=rec_resp.engine,
        warnings=rec_resp.warnings,
        scored=scored,
        requested_n=requested_n,
        returned_n=len(scored),
        diversity_applied=diversity_applied,
        tradeoff=tradeoff,
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
        form = workflow._score_and_validate(form, process, body.requirement, chem_screen=True)
    return ManualFormulationResponse(formulation=form, warnings=warnings)
