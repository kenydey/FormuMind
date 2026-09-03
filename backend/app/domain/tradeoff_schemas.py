"""P1-R2 trade-off analysis schemas."""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .schemas import ObjectiveSpec

if TYPE_CHECKING:
    # Avoid a circular import with app.domain.schemas (DOEPlan is defined
    # after TradeOffAnalysis is imported there). Pydantic v2 resolves the
    # forward reference lazily on first use.
    from .schemas import DOEPlan

CandidateSource = Literal["llm_recommend", "offline", "manual", "optimize", "baybe"]
ConfidenceLevel = Literal["high", "medium", "low"]
ScenarioKind = Literal["best_performance", "lowest_cost", "balanced", "low_voc"]


class GroundingSummary(BaseModel):
    high_count: int = 0
    low_count: int = 0
    low_component_names: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)


class FormulationCandidateView(BaseModel):
    id: str
    name: str
    source: CandidateSource = "llm_recommend"
    predicted: dict[str, float] = Field(default_factory=dict)
    predicted_std: dict[str, float] = Field(default_factory=dict)
    cost_cny_per_kg: float | None = None
    score: float | None = None
    pareto: bool = False
    pareto_rank: int | None = None
    confidence: ConfidenceLevel = "medium"
    grounding: GroundingSummary = Field(default_factory=GroundingSummary)
    warnings: list[str] = Field(default_factory=list)


class ScenarioPick(BaseModel):
    scenario: ScenarioKind
    candidate_id: str
    candidate_name: str
    rationale: str
    primary_metric: str = ""
    primary_value: float | None = None


class TradeOffAnalysis(BaseModel):
    objectives: list[ObjectiveSpec] = Field(default_factory=list)
    metric_columns: list[str] = Field(default_factory=list)
    pareto_frontier_ids: list[str] = Field(default_factory=list)
    candidates: list[FormulationCandidateView] = Field(default_factory=list)
    comparison_table: list[dict[str, object]] = Field(default_factory=list)
    scenario_picks: list[ScenarioPick] = Field(default_factory=list)
    dominance_notes: list[str] = Field(default_factory=list)
    engine: str = "predictor"
    # Third priority: minimal verification DOE per Pareto-front / scenario-pick
    # candidate, so the chemist can validate predictions with one click into
    # the workbench. Empty when verification_doe_enabled is False.
    verification_does: list["VerificationDoe"] = Field(default_factory=list)


class VerificationDoe(BaseModel):
    """A minimal DOE anchored to one recommended candidate for validation."""

    candidate_id: str
    candidate_name: str
    # Why verify this one / what to confirm (ties back to "why A not B").
    note: str
    # Ready-to-adopt DOE plan (frontend pushes via adoptDoePlanToWorkbench).
    doe_plan: DOEPlan


class RecommendMeta(BaseModel):
    requested_n: int = 0
    returned_n: int = 0
    diversity_applied: bool = False
