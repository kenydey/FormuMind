"""P1-R2 trade-off analysis schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .schemas import ObjectiveSpec

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


class RecommendMeta(BaseModel):
    requested_n: int = 0
    returned_n: int = 0
    diversity_applied: bool = False
