"""Project workspace schema — full NotebookLM-style project state persisted in SQLite."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .schemas import (
    AdaptiveDOEMetadata,
    ComprehensiveReport,
    DOEPlan,
    Evidence,
    Formulation,
    LoopReport,
    ModelInfo,
    ProductDomain,
    Requirement,
    ResearchResult,
)


class ProcessOptResultPayload(BaseModel):
    domain: str = ""
    iterations: int = 0
    engine: str = ""
    history: list[float] = Field(default_factory=list)
    best_params: dict[str, float] = Field(default_factory=dict)
    predicted_outcome: dict[str, float] = Field(default_factory=dict)


class ProjectWorkspace(BaseModel):
    """Complete UI workspace for one research project."""

    search_query: str = ""
    source_types: list[str] = Field(default_factory=lambda: ["patents", "literature", "internet"])
    sources: list[Evidence] = Field(default_factory=list)
    selected_sources: list[str] = Field(default_factory=list)
    chat_history: list[dict[str, Any]] = Field(default_factory=list)
    deep_report: ComprehensiveReport | None = None
    requirement: Requirement | None = None
    active_constraints: list[str] = Field(default_factory=list)
    research: ResearchResult | None = None
    leaderboard: list[Formulation] = Field(default_factory=list)
    doe_plan: DOEPlan | None = None
    adaptive_doe: AdaptiveDOEMetadata | None = None
    measured: dict[str, float] = Field(default_factory=dict)
    models: list[ModelInfo] = Field(default_factory=list)
    model_history: list[list[ModelInfo]] = Field(default_factory=list)
    train_message: str = ""
    campaign_state: str | None = None
    workbench_campaign_id: int | None = None
    workbench_objectives_snapshot: list[dict] = Field(default_factory=list)
    optimization_history: list[float] = Field(default_factory=list)
    loop_report: LoopReport | None = None
    rmse_history: list[dict[str, float]] = Field(default_factory=list)
    process_opt_result: ProcessOptResultPayload | None = None
    doe_engine: str = "auto"
    al_engine: str = "auto"
    optimize_engine: str = "auto"
    loop_doe_engine: str = "auto"
    recommend_source_types: list[str] = Field(default_factory=lambda: ["patents", "literature", "internet"])
    last_al_engine: str | None = None
    auto_loop_on_sync: bool = False


class ProjectSummary(BaseModel):
    id: str
    title: str
    headline: str
    domain: str
    created_at: datetime
    updated_at: datetime
    source_count: int = 0
    chat_count: int = 0
    leaderboard_count: int = 0
    has_doe: bool = False
    has_optimize: bool = False
    has_loop: bool = False
    has_process_opt: bool = False


class ProjectDetail(BaseModel):
    id: str
    title: str
    headline: str
    domain: str
    created_at: datetime
    updated_at: datetime
    workspace: ProjectWorkspace


class ProjectCreateRequest(BaseModel):
    title: str = ""
    requirement: Requirement | None = None


class ProjectUpdateRequest(BaseModel):
    workspace: ProjectWorkspace
    title: str | None = None


class LegacySnapshotPayload(BaseModel):
    """Import from browser localStorage SessionSnapshot."""

    id: str
    timestamp: str
    domain: str
    headline: str
    requirement: dict[str, Any]
    leaderboard: list[dict[str, Any]] = Field(default_factory=list)
    models: list[dict[str, Any]] = Field(default_factory=list)
    optimization_history: list[float] = Field(default_factory=list)


class MigrateLocalRequest(BaseModel):
    snapshots: list[LegacySnapshotPayload] = Field(default_factory=list)


def default_requirement() -> Requirement:
    from ..pipeline.workflow import default_objectives

    return Requirement(
        project_id="anticorrosion_coating",
        product_type="防腐蚀环氧底漆",
        application="carbon_steel",
        domain=ProductDomain.anticorrosion_coating,
        salt_spray_hours=500,
        film_weight_gsm=70,
        cure_temperature_c=80,
        cleaning_efficiency=90,
        voc_limit_gpl=420,
        objectives=default_objectives(ProductDomain.anticorrosion_coating),
    )


def workspace_from_legacy(snap: LegacySnapshotPayload) -> ProjectWorkspace:
    req = Requirement(**snap.requirement)
    return ProjectWorkspace(
        requirement=req,
        leaderboard=[Formulation(**f) for f in snap.leaderboard],
        models=[ModelInfo(**m) for m in snap.models],
        optimization_history=snap.optimization_history,
    )


def derive_title(workspace: ProjectWorkspace, fallback: str = "未命名项目") -> str:
    if workspace.search_query.strip():
        return workspace.search_query.strip()[:120]
    if workspace.requirement and workspace.requirement.product_type:
        return workspace.requirement.product_type[:120]
    return fallback


def derive_headline(workspace: ProjectWorkspace) -> str:
    if workspace.requirement:
        return workspace.requirement.headline()
    return "FormuMind project"


def summary_stats(workspace: ProjectWorkspace) -> dict[str, Any]:
    return {
        "source_count": len(workspace.sources),
        "chat_count": len(workspace.chat_history),
        "leaderboard_count": len(workspace.leaderboard),
        "has_doe": workspace.doe_plan is not None,
        "has_optimize": len(workspace.optimization_history) > 0,
        "has_loop": workspace.loop_report is not None,
        "has_process_opt": workspace.process_opt_result is not None,
    }
