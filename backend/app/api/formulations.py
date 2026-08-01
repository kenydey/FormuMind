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
    """LLM structured formulation recommend grounded on KB evidence.

    Mode selectable via ``FORMUMIND_FORMULATION_MODE``:
    - ``hybrid`` (default): KB retrieval → LLM synthesis (叠加)
    - ``llm_only``: pure LLM, no KB lookup (fast / offline fallback)
    - ``kb_only``: KB retrieval only, no LLM (degraded / validation)
    """
    from ..config import get_settings
    from ..pipeline.research_graph import resolve_grounded_evidence
    from ..services.grounded_recommend import ground_recommended_formulas
    from ..services.recommend_pipeline import finalize_recommendation_bundle, llm_candidate_count, resolve_recommend_n

    settings = get_settings()
    objectives = body.objectives or normalize_objectives(body.requirement)
    requested_n = resolve_recommend_n(body.n, settings=settings)
    llm_n = llm_candidate_count(requested_n, settings=settings)
    query = body.requirement.headline()
    mode = settings.formulation_mode

    # ── Step 1: KB retrieval (hybrid / kb_only modes) ──
    evidence: list = body.sources or []
    retrieve_ok = False
    if mode in ("hybrid", "kb_only"):
        try:
            grounded = resolve_grounded_evidence(
                body.requirement, query, pre_index=body.sources or None)
            evidence = grounded.grounded_evidence or evidence
            retrieve_ok = bool(grounded.grounded_evidence)
            if retrieve_ok:
                log.info("KB retrieval OK: %d evidence items (mode=%s)",
                         len(evidence), mode)
        except Exception as exc:
            log.warning("KB retrieval failed (mode=%s): %s", mode, exc)
            if mode == "kb_only":
                raise HTTPException(
                    status_code=503,
                    detail=f"知识库检索失败（mode=kb_only）：{exc}"
                ) from exc
            # hybrid → fall through to LLM-only
            evidence = body.sources or []
            retrieve_ok = False

    # ── Step 2: LLM synthesis (hybrid / llm_only) ──
    if mode in ("hybrid", "llm_only"):
        try:
            rec_resp = llm.recommend_formulations(
                body.requirement, objectives, evidence, n=llm_n)
        except Exception as exc:
            log.exception("recommend_formulations LLM failed")
            raise HTTPException(status_code=503, detail="配方推荐失败") from exc
    else:
        # kb_only: return evidence as lightweight recommendations
        rec_resp = _evidence_as_recommendation_response(
            evidence, body.requirement, objectives)

    if not rec_resp.formulas:
        raise HTTPException(status_code=503, detail="No formulations produced")

    grounded_formulas, ground_warnings = ground_recommended_formulas(
        rec_resp.formulas, evidence)
    rec_resp.warnings.extend(ground_warnings)

    if retrieve_ok:
        rec_resp.warnings.append(
            f"已从知识库检索 {len(evidence)} 条证据辅助推荐")

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


def _evidence_as_recommendation_response(
    evidence: list,
    requirement: Requirement,
    objectives: list[ObjectiveSpec],
) -> RecommendedFormulaListResponse:
    """Convert KB evidence into a basic recommendation response (kb_only mode)."""
    from ..domain.schemas import ProductDomain
    formulas: list[RecommendedFormula] = []
    for i, ev in enumerate(evidence[:5]):
        formulas.append(RecommendedFormula(
            name=ev.title or f"KB 候选 #{i+1}",
            domain=requirement.domain,
            rationale=f"知识库检索结果：{ev.snippet[:200]}" if ev.snippet else "无详细描述",
            objectives_summary="; ".join(
                f"{o.metric}: {o.direction}" for o in objectives),
            predicted={"salt_spray_hours": 0},
            score=ev.relevance or 0.5,
        ))
    return RecommendedFormulaListResponse(
        formulas=formulas, engine="kb_only",
        warnings=["kb_only 模式：仅返回知识库检索结果，未经 LLM 合成"],
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


# ── Revision history ─────────────────────────────────────────────────────────
# Formulations were overwritten in place inside a project payload, so the
# reasoning behind each revision was lost as soon as the next one was saved.


class SaveVersionRequest(BaseModel):
    formulation: Formulation
    lineage_id: str | None = None
    parent_version_id: str | None = None
    change_summary: str = ""
    created_by: str = ""
    project_id: str | None = None


class VersionView(BaseModel):
    id: str
    lineage_id: str
    version: int
    parent_version_id: str | None = None
    name: str
    domain: str
    change_summary: str = ""
    created_by: str = ""
    created_at: str | None = None
    project_id: str | None = None


class VersionDetail(VersionView):
    snapshot: dict = Field(default_factory=dict)


class LineageResponse(BaseModel):
    lineage_id: str
    versions: list[VersionView] = Field(default_factory=list)


class IngredientChangeView(BaseModel):
    name: str
    change: str
    role: str = ""
    before_pct: float | None = None
    after_pct: float | None = None
    delta_pct: float | None = None


class DiffResponse(BaseModel):
    from_version: int
    to_version: int
    change_summary: str = ""
    topology_changed: bool = False
    renamed: list[str] | None = None
    ingredient_changes: list[IngredientChangeView] = Field(default_factory=list)
    metric_deltas: dict = Field(default_factory=dict)


def _to_view(row) -> VersionView:
    return VersionView(
        id=row.id,
        lineage_id=row.lineage_id,
        version=row.version,
        parent_version_id=row.parent_version_id,
        name=row.name,
        domain=row.domain,
        change_summary=row.change_summary or "",
        created_by=row.created_by or "",
        created_at=row.created_at.isoformat() if row.created_at else None,
        project_id=row.project_id,
    )


@router.post("/formulations/versions", response_model=VersionView)
def save_formulation_version(body: SaveVersionRequest) -> VersionView:
    """Append an immutable revision. Summarises the change when none is given."""
    from ..services.formulation_history import get_history_store

    try:
        row = get_history_store().save_version(
            body.formulation,
            lineage_id=body.lineage_id,
            parent_version_id=body.parent_version_id,
            change_summary=body.change_summary,
            created_by=body.created_by,
            project_id=body.project_id,
        )
    except Exception as exc:
        log.exception("save formulation version failed")
        raise HTTPException(status_code=500, detail="配方版本保存失败") from exc
    return _to_view(row)


@router.get("/formulations/versions", response_model=list[LineageResponse])
def find_formulation_lineages(
    name: str = "",
    domain: str = "",
    limit: int = 10,
) -> list[LineageResponse]:
    """Stored histories whose first revision matches — used to reconnect a
    formulation on screen to its lineage without carrying an id in the client."""
    from ..services.formulation_history import get_history_store

    store = get_history_store()
    out: list[LineageResponse] = []
    for lineage_id in store.find_lineages(name=name, domain=domain, limit=limit):
        rows = store.lineage(lineage_id)
        if rows:
            out.append(
                LineageResponse(lineage_id=lineage_id, versions=[_to_view(r) for r in rows])
            )
    return out


@router.get("/formulations/versions/{lineage_id}", response_model=LineageResponse)
def formulation_lineage(lineage_id: str) -> LineageResponse:
    """Every revision of one formulation, oldest first."""
    from ..services.formulation_history import get_history_store

    rows = get_history_store().lineage(lineage_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"未找到配方谱系：{lineage_id}")
    return LineageResponse(lineage_id=lineage_id, versions=[_to_view(r) for r in rows])


@router.get("/formulations/versions/detail/{version_id}", response_model=VersionDetail)
def formulation_version_detail(version_id: str) -> VersionDetail:
    from ..services.formulation_history import get_history_store

    row = get_history_store().get(version_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"未找到版本：{version_id}")
    return VersionDetail(**_to_view(row).model_dump(), snapshot=row.snapshot or {})


@router.get("/formulations/versions/{from_id}/diff/{to_id}", response_model=DiffResponse)
def diff_formulation_versions(from_id: str, to_id: str) -> DiffResponse:
    """What changed between two revisions — the question history exists to answer."""
    from ..services.formulation_history import get_history_store

    diff = get_history_store().diff_versions(from_id, to_id)
    if diff is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    return DiffResponse(
        from_version=diff.from_version,
        to_version=diff.to_version,
        change_summary=diff.change_summary,
        topology_changed=diff.topology_changed,
        renamed=list(diff.renamed) if diff.renamed else None,
        ingredient_changes=[
            IngredientChangeView(
                name=c.name, change=c.change, role=c.role,
                before_pct=c.before_pct, after_pct=c.after_pct, delta_pct=c.delta_pct,
            )
            for c in diff.ingredient_changes
        ],
        metric_deltas=diff.metric_deltas,
    )
