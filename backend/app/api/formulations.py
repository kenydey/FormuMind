"""Metadata endpoint: domains, substrates, DOE designs, and baseline templates."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..domain.examples import BUILTIN_METRICS, EXAMPLE_PROJECTS, ROLE_CATALOG, load_example
from ..domain.formulation_gate import validate_formulations
from ..domain.knowledge import baseline_formulation
from ..domain.objective_contract import normalize_objective, normalize_objectives
from ..domain.schemas import (
    Evidence,
    Formulation,
    ObjectiveSpec,
    ProductDomain,
    RecommendedFormula,
    RecommendedFormulaListResponse,
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
    from ..services.engines.status import engines_status
    from ..services.rag import embedding_status

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
        # Import probes — UI disables unavailable engines (auto always allowed).
        "engines": engines_status(),
        # Option A: embedding upgrade catalog (no Qdrant; reindex after switch).
        **embedding_status(),
    }


@router.get("/examples/{example_id}", response_model=Requirement)
def get_example_project(example_id: str) -> Requirement:
    try:
        return load_example(example_id)
    except KeyError as exc:
        # An unknown id (stale link, typo) is a missing resource, not a server error.
        raise HTTPException(status_code=404, detail=f"未知的示例项目：{example_id}") from exc


@router.get("/templates/{domain}", response_model=Formulation, include_in_schema=False)
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


# Upper bound on evidence echoed back in a recommend response.
_ECHO_EVIDENCE_CAP = 50


class RecommendFormulationsRequest(BaseModel):
    requirement: Requirement
    objectives: list[ObjectiveSpec] = Field(default_factory=list)
    sources: list[Evidence] = Field(default_factory=list)
    n: int | None = Field(default=None, ge=1, le=12)
    include_tradeoff: bool = True
    scenario_kinds: list[str] = Field(default_factory=list)
    # 关系洞察开关：true 时对候选配方成分按需跑替代品/关系（默认关，省 token）
    relation_insight: bool = False
    # Soft bias only — NEVER a hard materials-only filter.
    prefer_materials_catalog: bool = False


class RecommendFormulationsResponse(BaseModel):
    formulas: list[RecommendedFormula]
    engine: str
    warnings: list[str] = Field(default_factory=list)
    scored: list[Formulation] = Field(default_factory=list)
    requested_n: int | None = None
    returned_n: int | None = None
    diversity_applied: bool = False
    tradeoff: TradeOffAnalysis | None = None
    relation_insights: list[dict] = Field(default_factory=list)
    # C-8: id of this recommendation round — the client sends it back on
    # POST /formulations/recommend/{recommend_id}/adopt for outcome telemetry.
    recommend_id: str = ""
    # Evidence the round was grounded on (KB retrieval, else the caller's own
    # sources). The async research task forwards it as ``research.evidence`` and
    # hands it to the KB-ingest dispatcher; before this field existed both read
    # a key the response never carried, so they always saw an empty list.
    grounded_evidence: list[Evidence] = Field(default_factory=list)
    # v20-3: 目标定义（含单位、方向），前端用此标注 raw_metrics 的列。
    # Formulation.score 是 0-1 归一化分（用于排序），
    # Formulation.predicted 是原始目标值（如盐雾 720h）。
    objectives: list[dict] = Field(default_factory=list)


def _resolve_request_objectives(body: RecommendFormulationsRequest) -> list[ObjectiveSpec]:
    """v25-fix: 显式 objectives 也要过别名规范化（v23 第 4 处绕过补齐）。

    用户显式传 ``objectives: [{"metric": "salt spray"}]`` 时 metric 别名
    （"salt spray" → "salt_spray_hours"）必须被解析，否则下游
    ``multi_objective_score`` 按别名取 0.0，静默打出错误排序。

    v27 P1-8: 显式 objectives 强校验 —— 别名解析后仍不在 KNOWN_METRICS
    的 metric 直接 422（提示合法取值），不再静默 0 分错排。
    requirement 派生的默认目标是可信源，不校验。

    v28 RC-1: domain 适用性校验 —— metric 合法但当前 domain 的 predictor
    不产出时，下游 props.get(metric, 0.0) 静默 0 分错排（P1-8 只拦了完全
    未知 metric，domain 错配是同类缺口的另一半），同样 422。
    """
    from ..domain.objective_contract import KNOWN_METRICS, domain_applicable_metrics

    if body.objectives:
        resolved = [normalize_objective(o) for o in body.objectives]
        unknown = [o.metric for o in resolved if o.metric not in KNOWN_METRICS]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"未知的优化指标: {', '.join(unknown)}。"
                    f"合法取值: {', '.join(sorted(KNOWN_METRICS))}"
                ),
            )
        domain = getattr(body.requirement, "domain", None)
        if domain is not None:
            applicable = domain_applicable_metrics(domain)
            mismatched = [o.metric for o in resolved if o.metric not in applicable]
            if mismatched:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"指标 {', '.join(mismatched)} 不适用于当前 domain"
                        f"（{domain.value}），该 predictor 不产出这些指标。"
                        f"该 domain 可用指标: {', '.join(sorted(applicable))}"
                    ),
                )
        return resolved
    return normalize_objectives(body.requirement)


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
    from ..services.recommend_pipeline import (
        llm_candidate_count,
        resolve_recommend_n,
        run_recommend_orchestration,
    )

    settings = get_settings()
    objectives = _resolve_request_objectives(body)
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
                    detail="知识库检索失败（mode=kb_only）"
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
            if mode == "hybrid" and evidence:
                # KB 已有证据：降级为 KB-only 推荐，不让 LLM 故障整体 503（A8）
                log.warning("LLM 合成失败，hybrid 降级为 KB-only: %s", exc)
                rec_resp = _offline_recommendation_response(
                    body.requirement, evidence, n=llm_n,
                    note="LLM 合成失败，已降级为离线模板候选 + 知识库证据校验",
                )
            else:
                log.exception("recommend_formulations LLM failed")
                raise HTTPException(status_code=503, detail="配方推荐失败") from exc
    else:
        # kb_only: no LLM. Retrieved evidence is not a formulation, so candidates
        # come from the deterministic offline engine and the orchestration's
        # grounding step checks them against the retrieved KB evidence.
        rec_resp = _offline_recommendation_response(
            body.requirement, evidence, n=llm_n,
            note="kb_only 模式：未调用 LLM，候选来自确定性离线模板",
        )

    if not rec_resp.formulas:
        raise HTTPException(status_code=503, detail="未能生成配方：LLM 与离线模板均无可用候选")

    bundle = run_recommend_orchestration(
        body.requirement,
        evidence,
        requested_n=requested_n,
        objectives=objectives,
        include_tradeoff=body.include_tradeoff,
        scenario_kinds=body.scenario_kinds or None,
        prefer_materials_catalog=bool(body.prefer_materials_catalog),
        synth_override=rec_resp,
        # P1-1: llm_only 模式无证据，strict grounding 会误删所有成分
        strict_grounding=(mode != "llm_only"),
        settings=settings,
    )
    aligned, scored, tradeoff = bundle.aligned_formulas, bundle.scored, bundle.tradeoff
    diversity_applied = bundle.diversity_applied
    rec_resp.warnings = bundle.warnings

    # P1-2: 编排后空结果契约 —— 全灭时 503 带因，不返回 200 空列表
    if not aligned:
        raise HTTPException(
            status_code=503,
            detail="推荐编排后无可用候选: " + "; ".join(bundle.warnings[:3] or ["未知原因"]),
        )

    if retrieve_ok:
        rec_resp.warnings.append(
            f"已从知识库检索 {len(evidence)} 条证据辅助推荐")

    insights: list[dict] = []
    if body.relation_insight:
        insights = _relation_insights(aligned, settings)

    # C-8: register this round as not-yet-adopted (adopt_rate denominator).
    # Best-effort — telemetry must never break the recommendation path.
    try:
        from ..db import recommend_outcome_store
        from ..db.database import default_session_factory
        from ..db.session_utils import commit_session

        _factory = default_session_factory()
        with commit_session(_factory) as _s:
            recommend_outcome_store.register_round(
                _s,
                recommend_id=bundle.recommend_id,
                project_id=(body.requirement.project_id or None),
            )
    except Exception as exc:  # noqa: BLE001 — telemetry fail-open by contract
        logging.getLogger(__name__).warning(
            "recommend round registration failed (fail-open): %s", exc
        )

    return RecommendFormulationsResponse(
        formulas=aligned,
        engine=rec_resp.engine,
        warnings=rec_resp.warnings,
        scored=scored,
        requested_n=requested_n,
        returned_n=len(scored),
        diversity_applied=diversity_applied,
        tradeoff=tradeoff,
        relation_insights=insights,
        recommend_id=bundle.recommend_id,
        grounded_evidence=list(evidence)[:_ECHO_EVIDENCE_CAP],
        # v20-3: 返回目标定义，前端用此标注 predicted 的原始值列。
        objectives=[
            {
                "metric": o.metric,
                "direction": o.direction,
                "weight": o.weight,
                "target_value": o.target_value,
                "unit": getattr(o, "unit", None),
            }
            for o in objectives
        ],
    )


class AdoptRecommendationBody(BaseModel):
    adopt_signal: str = "button"  # button | copied | campaign（C-a 只接 button）
    formula_index: int | None = None
    formula_snapshot: dict = Field(default_factory=dict)
    project_id: str | None = None


@router.get("/formulations/recommend/{recommend_id}/outcome")
def recommendation_outcome(recommend_id: str) -> dict:
    """P1-10: 双层信号水合 —— 页面刷新后徽标从后端重读，Datalab 验证
    回写也能被前端看到（不再只靠组件局部 useState）。"""
    from ..db import recommend_outcome_store
    from ..db.database import default_session_factory

    factory = default_session_factory()
    with factory() as session:
        row = recommend_outcome_store.get_outcome(session, recommend_id=recommend_id)
    if row is None:
        return {
            "recommend_id": recommend_id,
            "adopted": False,
            "adopt_signal": None,
            "experiment_validated": False,
            "formula_name": None,
        }
    snap = row.formula_snapshot or {}
    return {
        "recommend_id": row.recommend_id,
        "adopted": bool(row.adopted),
        "adopt_signal": row.adopt_signal,
        "experiment_validated": bool(getattr(row, "experiment_validated", False)),
        # P1-10: 采纳是配方粒度 —— 快照名供前端卡片对号，避免同轮
        # 未采纳的卡片刷新后也被点亮。
        "formula_name": snap.get("name") if isinstance(snap, dict) else None,
    }


@router.post("/formulations/recommend/{recommend_id}/adopt")
def adopt_recommendation(recommend_id: str, body: AdoptRecommendationBody) -> dict:
    """C-8: record a user adopt signal for one recommendation round.

    Idempotent per ``recommend_id`` — repeated adopts update the row instead
    of duplicating it. This is the weak-success layer (user adoption); the
    strong-success layer (experiment validation) is pending C-5.
    """
    from ..db import recommend_outcome_store
    from ..db.database import default_session_factory
    from ..db.session_utils import commit_session

    try:
        recommend_outcome_store.validate_adopt_signal(body.adopt_signal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    factory = default_session_factory()
    with commit_session(factory) as session:
        # formula_hash is derived from the snapshot inside the store.
        row = recommend_outcome_store.record_adopt(
            session,
            recommend_id=recommend_id,
            project_id=body.project_id or None,
            adopt_signal=body.adopt_signal,
            formula_snapshot=body.formula_snapshot,
        )
        # P2: 先 sync、后 adopt —— 采纳时已有 lab 测量的实验行若匹配，
        # 在此补记强成功层（fail-open，不影响 adopt 主路径）。
        recommend_outcome_store.try_validate_adopt_against_lab(
            session,
            recommend_id=recommend_id,
            project_id=body.project_id or None,
            snapshot=body.formula_snapshot,
        )
    return {
        "recommend_id": row.recommend_id,
        "adopted": row.adopted,
        "adopt_signal": row.adopt_signal,
        # U-4: 双层口径 —— 用户采纳 + 实验验证（旧库无该列时回退 False）。
        "experiment_validated": bool(getattr(row, "experiment_validated", False)),
    }


def _relation_insights(formulas: list, settings) -> list[dict]:
    """候选配方成分 → 实体 → 替代品/关系洞察（relation_insight 按需）。

    只对候选成分解析出的实体做关系查询，不做全库关系提取 —— token 开销
    与候选成分数成正比，而不是与库大小成正比。

    P1-5: 加总量上限（最多 20 个成分）与总超时（60s），防无界串行查询。
    """
    import time

    from ..services.kg.entity_resolver import resolve_query
    from ..services.kg.graph_query import discover_substitutes, get_entity_relations

    insights: list[dict] = []
    seen: set[str] = set()
    _deadline = time.monotonic() + 60
    _max_components = 20
    for f in formulas:
        for comp in (f.components or []):
            if len(seen) >= _max_components or time.monotonic() > _deadline:
                return insights
            key = (comp.cas_no or comp.name or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            try:
                resolved = resolve_query(key, settings=settings)
            except Exception:
                continue
            for eid in (resolved.expanded_entity_ids or [])[:3]:
                try:
                    subs = discover_substitutes(eid, limit=5)
                    rels = get_entity_relations(eid, limit=8)
                except Exception:
                    continue
                sub_names = [c.entity_name for c in (subs.substitutes or []) if c.entity_name]
                if not sub_names and not rels:
                    continue
                insights.append(
                    {
                        "component": comp.name,
                        "cas_no": comp.cas_no,
                        "substitutes": sub_names,
                        "relations": [
                            {
                                "type": str(r.relation_type),
                                "target": r.target_entity_id,
                                "confidence": r.confidence,
                            }
                            for r in rels
                        ],
                    }
                )
    return insights


def _offline_recommendation_response(
    requirement: Requirement,
    evidence: list,
    *,
    n: int,
    note: str,
) -> RecommendedFormulaListResponse:
    """No-LLM candidates for ``kb_only`` and for a hybrid run whose LLM failed.

    This used to turn each retrieved *evidence snippet* into a component-less
    ``RecommendedFormula``. The orchestration (``recommended_to_formulation``)
    rejects a formula without components, so ``kb_only`` always answered 200
    with zero formulas plus a "has no components" warning per document — or a
    503 when retrieval came back empty — and the degraded-hybrid path claimed to
    avoid a 503 while delivering nothing. Candidates now come from the
    deterministic offline engine (the same one the LLM path falls back to); the
    orchestration's grounding step then checks their components against the
    retrieved evidence and the material catalog.
    """
    from ..domain.formulation_gate import offline_recommend_response
    from ..domain.knowledge import offline_recommend_fallback

    resp = offline_recommend_response(
        offline_recommend_fallback(requirement, n=n), reason=note
    )
    if evidence:
        resp.warnings.append(
            f"已按检索到的 {len(evidence)} 条知识库证据校验候选成分"
            "（证据与材料库均未覆盖的成分会被剔除）"
        )
    else:
        resp.warnings.append(
            "知识库未检索到匹配证据：候选仅来自离线模板，成分未经文献/专利证据校验"
        )
    return resp


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
    if not forms:
        # validation drops a recipe it cannot keep (e.g. no ingredients at all): that is the caller's
        # input, not a server fault — it used to surface as ``IndexError`` → HTTP 500.
        raise HTTPException(
            status_code=422,
            detail="配方未通过校验：" + ("；".join(warnings) if warnings else "没有可用的成分"),
        )
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

    # W2-6 (P1-2): link formulation version -> claim (change summary).
    # Fail-open: provenance must never break version saving.
    try:
        from ..services import provenance as _prov

        if (body.change_summary or "").strip():
            _prov.link(
                "formulation",
                row.id,
                "claim",
                _prov.claim_id_for_text(body.change_summary),
                "summarised_by",
            )
    except Exception:
        pass

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


@router.get("/formulations/versions/detail/{version_id}", response_model=VersionDetail, include_in_schema=False)
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


# ── P3.1: embodiment drafts from ingested fulltext ────────────────────


class EmbodimentEligibilityRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list)


class ExtractEmbodimentDraftRequest(BaseModel):
    source_id: str = Field(min_length=1)
    domain: str = "anticorrosion_coating"
    surechembl_hint: bool = False


class ConfirmEmbodimentDraftRequest(BaseModel):
    draft: dict


@router.post("/formulations/embodiment-eligibility")
def embodiment_eligibility(body: EmbodimentEligibilityRequest) -> dict:
    """Batch eligibility for KB fulltext embodiment extract."""
    from ..services import embodiment_drafts as emb

    ids = [str(s).strip() for s in (body.source_ids or []) if str(s).strip()]
    return emb.check_eligibility_many(ids[:100])


@router.post("/formulations/extract-embodiment-draft")
def extract_embodiment_draft(body: ExtractEmbodimentDraftRequest) -> dict:
    """Extract review-only draft from an ingested SourceDocument (no live fetch)."""
    from ..services import embodiment_drafts as emb

    out = emb.extract_embodiment_draft(
        source_id=body.source_id,
        domain=body.domain,
        surechembl_hint=body.surechembl_hint,
    )
    if not out.get("ok"):
        reason = out.get("reason") or "ineligible"
        code = 404 if reason == "not_ingested" else 400
        raise HTTPException(status_code=code, detail=reason)
    return out


@router.post("/formulations/confirm-embodiment-draft")
def confirm_embodiment_draft(body: ConfirmEmbodimentDraftRequest) -> dict:
    """Human confirm: KG + pending materials only (never production pool)."""
    from ..services import embodiment_drafts as emb

    out = emb.confirm_embodiment_draft(body.draft or {})
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("reason") or "confirm_failed")
    return out
