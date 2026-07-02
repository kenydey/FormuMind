"""Shared domain schemas — the data contracts that flow end to end through the
research → recommend → DOE → simulate → optimize pipeline.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ProductDomain(str, Enum):
    """The three metal surface treatment product families FormuMind targets."""

    anticorrosion_coating = "anticorrosion_coating"  # 防腐蚀涂料
    degreaser = "degreaser"  # 脱脂剂
    surface_treatment = "surface_treatment"  # 表面处理剂


class Substrate(str, Enum):
    carbon_steel = "carbon_steel"
    galvanized_steel = "galvanized_steel"
    aluminum = "aluminum"
    stainless_steel = "stainless_steel"
    magnesium_alloy = "magnesium_alloy"


class ObjectiveDirection(str, Enum):
    maximize = "maximize"
    minimize = "minimize"
    match_target = "match_target"


class ObjectiveSpec(BaseModel):
    """One term of a multi-objective optimization goal (schema-driven contract)."""

    id: str = ""
    metric: str  # machine key, e.g. salt_spray_hours
    display_name: str = ""
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    direction: str = Field(default="maximize")
    ref_min: float | None = None
    ref_max: float | None = None
    target_value: float | None = None
    unit: str = ""
    value_type: Literal["number", "rating"] = "number"

    @model_validator(mode="after")
    def _fill_defaults(self) -> ObjectiveSpec:
        if not self.id:
            object.__setattr__(self, "id", self.metric or uuid4().hex[:8])
        if self.direction not in {d.value for d in ObjectiveDirection}:
            object.__setattr__(self, "direction", "maximize")
        return self


class LeverSpec(BaseModel):
    """A tunable DOE/optimization factor (ingredient wt% or process parameter)."""

    name: str
    low: float
    high: float
    unit: str = "wt%"


class MaterialSpec(BaseModel):
    """Project-level raw material entry (extends global RAW_MATERIALS)."""

    name: str
    role: str = "additive"
    weight_pct: float = Field(default=0.0, ge=0, le=100)
    smiles: str | None = None
    formula: str | None = None
    price_cny_per_kg: float | None = None
    voc_contrib: float | None = None


class MetricPriorSpec(BaseModel):
    """Declarative prior for a custom metric (YAML or builtin alias)."""

    metric: str
    prior_yaml: str | None = None
    builtin_alias: str | None = None
    confidence: str = "cold-start"


class Requirement(BaseModel):
    """User-supplied R&D requirement captured from the left input panel."""

    project_id: str = ""
    product_type: str = ""
    application: str = ""
    domain: ProductDomain
    substrate: Substrate = Substrate.carbon_steel
    # Anti-corrosion targets
    salt_spray_hours: float = Field(0, ge=0, description="Target neutral salt spray resistance (h)")
    film_weight_gsm: float = Field(0, ge=0, description="Target dry film weight / coating weight (g/m^2)")
    cure_temperature_c: float | None = Field(80, ge=0, le=400, description="Max acceptable cure temperature (C)")
    # Degreaser targets
    cleaning_efficiency: float = Field(0, ge=0, le=100, description="Target soil removal (%)")
    # Common constraints
    voc_limit_gpl: float | None = Field(420, ge=0, description="Max VOC (g/L); None disables VOC limit checks")
    ph_target: float | None = Field(None, ge=0, le=14)
    notes: str = ""
    objectives: list[ObjectiveSpec] = Field(default_factory=list)
    levers: list[LeverSpec] = Field(default_factory=list)
    materials: list[MaterialSpec] = Field(default_factory=list)
    metric_priors: list[MetricPriorSpec] = Field(default_factory=list)
    constraint_values: dict[str, float] = Field(
        default_factory=dict,
        description="工艺约束 SSOT {名称: 值}，含 catalog 标签与自定义项",
    )
    active_formulation: Formulation | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_constraints(cls, data: Any) -> Any:
        """Merge deprecated ``constraints`` dict into ``constraint_values`` on load."""
        if not isinstance(data, dict):
            return data
        legacy = data.pop("constraints", None)
        if not legacy:
            return data
        cv = dict(data.get("constraint_values") or {})
        for key, raw in legacy.items():
            if raw is not None:
                cv.setdefault(key, float(raw))
        data["constraint_values"] = cv
        return data

    def headline(self) -> str:
        label = self.product_type or self.domain.value
        app = self.application or self.substrate.value
        bits = [label, f"on {app}"]
        if self.salt_spray_hours:
            bits.append(f"{self.salt_spray_hours:.0f}h salt spray")
        if self.film_weight_gsm:
            bits.append(f"{self.film_weight_gsm:.0f} g/m^2 film")
        if self.cleaning_efficiency:
            bits.append(f"{self.cleaning_efficiency:.0f}% cleaning")
        return ", ".join(bits)


class Ingredient(BaseModel):
    name: str
    role: str  # e.g. resin, hardener, inhibitor, surfactant, solvent, pigment
    smiles: str | None = None
    formula: str | None = None
    mf_structure: str | None = None
    cas_no: str | None = None
    zh_name: str | None = None
    molar_mass: float | None = None
    weight_pct: float = Field(ge=0, le=100)
    # Lab-table fields (LLM recommend / UI)
    component_type: str = ""
    equivalents: float | None = None
    mmol: float | None = None
    amount_display: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def _sync_formula_fields(self) -> Ingredient:
        if self.mf_structure and not self.formula:
            object.__setattr__(self, "formula", self.mf_structure)
        elif self.formula and not self.mf_structure:
            object.__setattr__(self, "mf_structure", self.formula)
        if not self.component_type and self.role:
            object.__setattr__(self, "component_type", self.role)
        return self


class Formulation(BaseModel):
    name: str
    domain: ProductDomain
    ingredients: list[Ingredient]
    rationale: str = ""
    source: str = ""  # e.g. "manual", "ai_modify", "recommend"
    predicted: dict[str, float] = Field(default_factory=dict)
    predicted_std: dict[str, float] = Field(default_factory=dict)
    prediction_tiers: dict[str, str] = Field(default_factory=dict)
    score: float | None = None
    warnings: list[str] = Field(default_factory=list)

    def total_pct(self) -> float:
        return round(sum(i.weight_pct for i in self.ingredients), 4)


class RecommendedFormulaComponent(BaseModel):
    """One row in the structured lab recipe table (LLM output + UI)."""

    component_type: str = ""
    name: str
    zh_name: str = ""
    cas_no: str = ""
    mf: str = ""
    smiles: str | None = None
    molar_mass: float | None = None
    equivalents: float | None = None
    mmol: float | None = None
    amount_display: str = ""
    weight_pct: float | None = Field(default=None, ge=0, le=100)
    notes: str = ""


class RecommendedFormula(BaseModel):
    """Structured formulation recommendation from the LLM recommend engine."""

    name: str
    domain: ProductDomain
    rationale: str = ""
    objectives_summary: str = ""
    components: list[RecommendedFormulaComponent] = Field(default_factory=list)
    predicted: dict[str, float] = Field(default_factory=dict)
    score: float | None = None
    warnings: list[str] = Field(default_factory=list)
    engine: Literal["llm", "offline"] = "llm"


class RecommendedFormulaListResponse(BaseModel):
    """Wrapper for LLM structured output and API responses."""

    formulas: list[RecommendedFormula] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    engine: Literal["llm", "offline"] = "offline"


class Evidence(BaseModel):
    """A retrieved patent or literature snippet with a citation."""

    source: str  # USPTO / EPO / literature / seed
    identifier: str
    title: str
    snippet: str
    relevance: float = Field(ge=0, le=1)
    is_seed_corpus: bool = False


class ParameterBoundary(BaseModel):
    min_value: float | None = Field(None, description="最小值")
    max_value: float | None = Field(None, description="最大值")
    unit: str = Field(..., description="单位，如 g/L, °C, % 等")

    @model_validator(mode="after")
    def check_physical_laws(self) -> "ParameterBoundary":
        lo, hi = self.min_value, self.max_value
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"物理定律冲突: {lo} > {hi}")
        if lo is not None and lo < 0 and self.unit.strip() in ("g/L", "mg/L", "kg", "%", "wt%"):
            raise ValueError(f"浓度不能为负: {lo}{self.unit}")
        return self


class SourceGuideSchema(BaseModel):
    summary: str = Field(..., max_length=600, description="300字内核心化学机理与工艺摘要")
    key_entities: list[str] = Field(..., min_length=1, max_length=30)
    parameter_space: dict[str, ParameterBoundary] = Field(default_factory=dict)
    faqs: list[str] = Field(..., min_length=1, max_length=5)
    status: Literal["verified", "degraded"] = "verified"

    @model_validator(mode="after")
    def _trim_summary(self) -> "SourceGuideSchema":
        if len(self.summary) > 300:
            self.summary = self.summary[:300]
        return self

    @classmethod
    def degraded(cls, reason: str = "提取失败") -> "SourceGuideSchema":
        return cls(
            summary=reason,
            key_entities=["未知"],
            parameter_space={},
            faqs=["该文献结构化提取未成功，请人工审核"],
            status="degraded",
        )


class IngestResult(BaseModel):
    filename: str
    evidence: list[Evidence]
    total: int
    source_id: str | None = None
    source_guide: SourceGuideSchema | None = None
    extraction_status: Literal["ok", "skipped", "failed", "degraded"] = "skipped"


class ResearchResult(BaseModel):
    requirement_headline: str
    evidence: list[Evidence]
    mechanism: str
    recommended: list[Formulation]
    chat_markdown: str
    recommend_engine: Literal["llm", "offline"] = "offline"


class ComprehensiveReport(BaseModel):
    """Output of the DeepResearchEngine deep-research orchestrator.

    A citation-grounded research report synthesised by cross-validating web and
    knowledge-base evidence, plus candidate formulations for the topic.
    """

    topic: str
    report_markdown: str
    citations: list[Evidence] = Field(default_factory=list)
    candidates: list[Formulation] = Field(default_factory=list)
    web_count: int = 0
    kb_count: int = 0
    engine: str = "offline"  # "llm" | "offline"
    verified_claims: list[dict] = Field(default_factory=list)
    claim_check_engine: str = "offline"
    claim_check_pass_rate: float = 1.0


class DeepResearchRequest(Requirement):
    """Requirement plus a free-text research topic for /api/research/deep.

    ``topic`` drives multi-source retrieval and synthesis; when empty the
    requirement headline is used instead.
    """

    topic: str = ""
    source_types: list[str] = Field(
        default_factory=lambda: ["patents", "literature", "internet"],
        description="与 /api/search 相同的信息源类别",
    )


class DOEFactor(BaseModel):
    name: str
    low: float
    high: float
    unit: str = ""


class DOERun(BaseModel):
    run_id: int
    coded: dict[str, float]
    natural: dict[str, float]
    ai_suggested: bool = False


class DOEPlan(BaseModel):
    design: str  # full_factorial / fractional_factorial / plackett_burman / ccd / lhs
    factors: list[DOEFactor]
    runs: list[DOERun]
    notes: str = ""
    plan_id: str = ""  # assigned + cached by the workflow for export/round-trip
    domain: ProductDomain | None = None  # carried so exported runs round-trip on import


class BaybeRecommendResult(BaseModel):
    """Stateless baybe roundtrip payload (not persisted to DB)."""

    plan: DOEPlan
    campaign_state: str
    engine: str = "baybe"


class ActiveDoeResult(BaseModel):
    """Active-learning DOE response — plan plus optional baybe campaign state."""

    plan: DOEPlan
    campaign_state: str | None = None
    engine: str = "legacy"


class OptimizationResult(BaseModel):
    iterations: int
    objective: str
    objectives: list[ObjectiveSpec] = Field(default_factory=list)
    history: list[float]
    top_formulations: list[Formulation]
    # Which optimizer engine produced this result (e.g. "numpy-ucb",
    # "optuna-tpe", "summit-sobo", "botorch-ei"). Default preserves
    # backward compatibility.
    engine: str = "numpy-ucb"


class TaskState(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class TaskStatus(BaseModel):
    task_id: str
    kind: str
    state: TaskState
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] | None = None
    stream_url: str = ""


class AsyncTaskAccepted(BaseModel):
    """HTTP 202 response when a Celery task is enqueued."""

    task_id: str
    stream_url: str
    status_url: str


# Backward-compatible alias used by some API modules
TaskHandle = AsyncTaskAccepted


class ExperimentRecord(BaseModel):
    """A single measured DOE/lab result fed back into the platform.

    ``factors`` are the formulation levers in natural units (matching a DOE
    run's ``natural`` values, e.g. ingredient wt% and cure temperature), and
    ``measured`` holds the lab-observed property values keyed by metric name.
    """

    domain: ProductDomain
    project_id: str = ""
    factors: dict[str, float] = Field(default_factory=dict)
    cure_temperature_c: float | None = None
    measured: dict[str, float]
    source: str = "lab"
    label: str = ""


class ExperimentSubmission(BaseModel):
    records: list[ExperimentRecord]
    retrain: bool = True


class ModelInfo(BaseModel):
    domain: ProductDomain
    project_id: str = ""
    metric: str
    backend: str  # sklearn-rf / numpy-ridge
    n_samples: int
    r2: float
    cv_r2: float | None = None
    rmse: float


class TrainingReport(BaseModel):
    trained: list[ModelInfo]
    total_records: int
    message: str = ""


# ── v0.5: IP analysis ────────────────────────────────────────────────────────

class PatentRisk(BaseModel):
    patent_id: str
    title: str
    risk: str  # "high" | "medium" | "low" | "unknown"
    claim_overlap: str
    recommendation: str


class IPReport(BaseModel):
    formulation_name: str
    novelty_score: float  # 0=likely infringes … 1=highly novel
    risks: list[PatentRisk]
    whitespace_hints: list[str]
    raw_patents_searched: int
    engine: str  # "llm" | "offline-keyword"


class IPAnalysisRequest(BaseModel):
    formulation: Formulation
    limit_patents: int = 10


# ── v0.5: Process parameter optimization ────────────────────────────────────

class ProcessOptRequest(BaseModel):
    domain: ProductDomain
    iterations: int = 18
    objectives: list[ObjectiveSpec] = Field(default_factory=list)


class ProcessOptResult(BaseModel):
    domain: str
    iterations: int
    engine: str
    history: list[float]
    best_params: dict[str, float]
    predicted_outcome: dict[str, float]


# ── v0.6: Self-driving closed loop ───────────────────────────────────────────

class LoopRequest(Requirement):
    """Requirement extended with closed-loop iteration controls."""

    optimize_iterations: int = 24
    n_suggest: int = 4
    optimize_engine: str = "auto"
    doe_engine: str = "auto"


class LoopReport(BaseModel):
    """One turn of the experiment → retrain → optimize → next-DOE loop."""

    domain: str
    total_records: int
    model_info: list[ModelInfo] = Field(default_factory=list)
    rmse_by_metric: dict[str, float] = Field(default_factory=dict)
    optimization: OptimizationResult
    next_doe: DOEPlan
    engine: str


# ── v0.6: Natural-language intent parsing ────────────────────────────────────

class IntentParseRequest(BaseModel):
    text: str


class IntentResult(BaseModel):
    requirement: Requirement
    confidence: float  # 0..1 heuristic confidence
    extracted_fields: list[str]  # which Requirement fields were populated
    engine: str  # "llm" | "offline-heuristic"


# ── v0.8: Hierarchical multi-agent review ────────────────────────────────────
# All agent context and API responses are pure JSON (these pydantic models).
# The supervisor (InitializeAgent) dispatches to expert agents (Chemist,
# Inspector); each returns an AgentFinding; the supervisor aggregates them into
# a single ReviewVerdict.

class Recommendation(BaseModel):
    """A concrete remediation suggested by an expert agent."""

    kind: str  # "substitute_crosslinker" | "substitute_resin" | "swap_catalyst" | "remove" | "review"
    target: str | None = None  # the offending ingredient, if any
    suggestion: str  # the recommended material or action
    rationale: str  # why this remediation applies


class AgentIssue(BaseModel):
    """One problem detected by an expert agent."""

    code: str  # machine-readable, e.g. "isocyanate_water_incompatibility", "svhc"
    severity: str  # "high" | "medium" | "low"
    ingredient: str | None = None
    message: str
    recommendations: list[Recommendation] = Field(default_factory=list)


class AgentFinding(BaseModel):
    """The verdict from a single expert agent."""

    agent: str  # "chemist" | "inspector"
    status: str  # "pass" | "warn" | "intercept"
    issues: list[AgentIssue] = Field(default_factory=list)
    engine: str  # "deterministic" | "deterministic+llm"


class ReviewVerdict(BaseModel):
    """The aggregated verdict returned by the supervisor agent."""

    formulation_name: str
    overall_status: str  # "pass" | "warn" | "intercept" (worst of all findings)
    findings: list[AgentFinding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)  # merged + deduped
    engine: str  # "deterministic" | "deterministic+llm"


class AgentReviewRequest(BaseModel):
    formulation: Formulation
    requirement: Requirement | None = None
    explain: bool = True  # enable optional LLM explanation polish (skipped when no key)


class DatalabSampleResponse(BaseModel):
    """Datalab POST /new-sample/ sample_list_entry contract."""

    item_id: str
    name: str | None = None
    refcode: str | None = None
    status: str | None = None

    model_config = {"extra": "ignore"}


class DatalabItemEnvelope(BaseModel):
    """Datalab GET /get-item-data/{id} outer wrapper."""

    item_data: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class DatalabDeleteResponse(BaseModel):
    """Datalab POST /delete-sample/ response."""

    status: str

    model_config = {"extra": "ignore"}


Requirement.model_rebuild()
