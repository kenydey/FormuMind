// Typed backend client. Mirrors the FastAPI domain schemas.

export type ProductDomain = "anticorrosion_coating" | "degreaser" | "surface_treatment";

export interface ObjectiveSpec {
  id?: string;
  metric: string;
  display_name?: string;
  weight: number;
  direction: "maximize" | "minimize" | "match_target";
  target_value?: number | null;
  unit?: string;
  ref_min?: number | null;
  ref_max?: number | null;
  value_type?: "number" | "rating";
}

export interface LeverSpec {
  name: string;
  low: number;
  high: number;
  unit?: string;
}

export interface MaterialSpec {
  name: string;
  role: string;
  weight_pct?: number;
  smiles?: string | null;
}

export interface Requirement {
  project_id?: string;
  product_type?: string;
  application?: string;
  domain: ProductDomain;
  substrate: string;
  salt_spray_hours: number;
  film_weight_gsm: number;
  cure_temperature_c: number | null;
  cleaning_efficiency: number;
  voc_limit_gpl: number | null;
  ph_target: number | null;
  notes: string;
  objectives: ObjectiveSpec[];
  levers?: LeverSpec[];
  materials?: MaterialSpec[];
  constraint_values?: Record<string, number>;
  /** @deprecated migrated to constraint_values on load */
  constraints?: Record<string, number | null>;
  active_formulation?: Formulation | null;
}

export interface ChemicalLookupResult {
  query: string;
  cas: string;
  iupac_name: string;
  zh_name: string;
  formula: string;
  smiles?: string;
  molar_mass?: number;
}

/** Full dossier from /api/chemical/profile — superset of the lookup payload. */
export interface ChemicalProfile {
  query: string;
  cas: string;
  iupac_name: string;
  zh_name: string;
  formula: string;
  smiles?: string | null;
  molar_mass?: number | null;
  found: boolean;
  source: string;
  func_groups: string[];
  /** True=分子已见于专利文献（molbloom）, False=novel, null=unknown */
  patented: boolean | null;
  safety: { controlled: boolean | null; explosive: boolean | null };
  synthetic_accessibility?: { sa_score: number | null; tier: string; note?: string };
  chemtools: { enabled: boolean };
}

export interface ChemToolsCapability {
  available: boolean;
  hint?: string | null;
}

export interface ChemToolsStatus {
  enabled: boolean;
  rdkit_installed: boolean;
  pubchem_available?: boolean;
  molbloom_installed?: boolean;
  capabilities: Record<string, ChemToolsCapability>;
}

export interface Ingredient {
  name: string;
  zh_name?: string | null;
  role: string;
  weight_pct: number;
  formula?: string | null;
  mf_structure?: string | null;
  cas_no?: string | null;
  smiles?: string | null;
  molar_mass?: number | null;
  component_type?: string;
  equivalents?: number | null;
  mmol?: number | null;
  amount_display?: string;
  notes?: string;
  evidence_refs?: string[];
  grounding_confidence?: "high" | "low";
}

export interface Formulation {
  name: string;
  domain: ProductDomain;
  ingredients: Ingredient[];
  rationale: string;
  predicted: Record<string, number>;
  predicted_std: Record<string, number>;
  prediction_tiers?: Record<string, string>;
  // Real experiment measurements for this formulation when it corresponds to
  // a measured run (charts prefer these over `predicted`); absent for pure
  // predictions. Optional so existing Formulation producers are unaffected.
  measured?: Record<string, number>;
  // Ingredient/factor values in natural units (cross-project KG similarity
  // queries are built from these); absent when the formulation carries no
  // experiment factors.
  factors?: Record<string, number>;
  score: number | null;
  warnings: string[];
  source?: string;
  // KG compatibility adjustment detail (second priority). Populated by the
  // backend when KG is enabled; optional on the client side.
  kg_compat?: {
    feasible: boolean;
    status: string;
    incompatible_pairs: { a: string; b: string; relation: string }[];
    synergy_pairs: { a: string; b: string; relation: string }[];
    measured_materials?: string[];
    reasons: string[];
  } | null;
}

export interface EvidenceEntityRef {
  entity_id: string;
  kind: "chemical" | "trade_product" | "element" | "parameter";
  display_name: string;
  composition_status?: "resolved" | "partial" | "mixture" | "proprietary" | "unknown";
  surface_form?: string | null;
}

export interface Evidence {
  source: string;
  identifier: string;
  title: string;
  snippet: string;
  relevance: number;
  /** True when this row is from the offline seed corpus, not a live API hit. */
  is_seed_corpus?: boolean;
  entity_refs?: EvidenceEntityRef[];
}

export interface ResearchResult {
  requirement_headline: string;
  evidence: Evidence[];
  mechanism: string;
  recommended: Formulation[];
  chat_markdown: string;
  recommend_engine?: "llm" | "offline";
}

export interface RecommendedFormulaComponent {
  component_type?: string;
  name: string;
  cas_no?: string;
  mf?: string;
  smiles?: string | null;
  molar_mass?: number | null;
  equivalents?: number | null;
  mmol?: number | null;
  amount_display?: string;
  weight_pct?: number | null;
  notes?: string;
}

export interface RecommendedFormula {
  name: string;
  domain: ProductDomain;
  rationale?: string;
  objectives_summary?: string;
  components: RecommendedFormulaComponent[];
  predicted?: Record<string, number>;
  score?: number | null;
  warnings?: string[];
  engine?: "llm" | "offline";
}

export interface RecommendFormulationsResponse {
  formulas: RecommendedFormula[];
  engine: string;
  warnings: string[];
  scored: Formulation[];
  requested_n?: number;
  returned_n?: number;
  diversity_applied?: boolean;
  tradeoff?: TradeOffAnalysis | null;
}

export interface TradeOffAnalysis {
  objectives?: ObjectiveSpec[];
  metric_columns?: string[];
  pareto_frontier_ids?: string[];
  comparison_table?: Record<string, unknown>[];
  scenario_picks?: ScenarioPick[];
  dominance_notes?: string[];
  // Third priority: minimal verification DOE per Pareto-front / scenario-pick
  // candidate, ready to push into the workbench.
  verification_does?: VerificationDoe[];
}

export interface VerificationDoe {
  candidate_id: string;
  candidate_name: string;
  note: string;
  doe_plan: DOEPlan;
}

export interface ScenarioPick {
  scenario: string;
  candidate_id: string;
  candidate_name: string;
  rationale: string;
  primary_metric?: string;
  primary_value?: number | null;
}

export interface OptimizationResult {
  iterations: number;
  objective: string;
  objectives: ObjectiveSpec[];
  history: number[];
  top_formulations: Formulation[];
  engine?: string;
}

export interface RunExplanation {
  run_id: number;
  strategy: "exploration" | "exploitation" | "balanced" | "constraint_fill";
  summary: string;
  nearest_experiment_ids: string[];
  predicted_delta_pct?: number | null;
  acquisition_score?: number | null;
  constraint_warnings?: string[];
}

export interface AnomalyFlag {
  experiment_id: string;
  type: "high_residual" | "physical_limit" | "outlier_in_factor_space";
  severity: "info" | "warning" | "critical";
  note: string;
  predicted?: number | null;
  actual?: number | null;
}

export interface AdaptiveDOEMetadata {
  strategy_label: "exploration" | "balanced" | "exploitation";
  strategy_rationale: string;
  run_explanations: RunExplanation[];
  anomalies: AnomalyFlag[];
  recommended_next_action: string;
  budget_remaining?: number | null;
}

export interface ChemicalFeasibility {
  feasible: boolean;
  status: string;
  reasons: string[];
}

export interface PhysicalConstraints {
  feasible: boolean;
  status: string; // pass | warn | infeasible
  reasons: string[];
  acid_stability?: { status: string; reasons: string[] };
  compliance?: { status: string; reasons: string[] };
}

export interface ActiveDoeResult extends AdaptiveDOEMetadata {
  plan: DOEPlan;
  campaign_state: string | null;
  engine: string;
  // KG chemical-compatibility verdict for the shared formulation skeleton.
  chemical_feasibility?: ChemicalFeasibility | null;
  // v11: deterministic physical-constraint verdict (acid stability + compliance).
  physical_constraints?: PhysicalConstraints | null;
}

export type TaskProgressStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";

export interface TaskProgressEvent {
  status: TaskProgressStatus;
  stage?: string;
  message: string;
  progress?: number;
  data?: Record<string, unknown>;
  elapsed_ms?: number | null;
}

export interface AsyncTaskAccepted {
  task_id: string;
  stream_url: string;
  status_url: string;
}

export interface TaskStatus {
  task_id: string;
  kind: string;
  state: "pending" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  message: string;
  result: Record<string, unknown> | null;
  stream_url?: string;
  stage?: string;
  elapsed_ms?: number | null;
}

export interface DOEFactor {
  name: string;
  low: number;
  high: number;
  unit: string;
}

export interface DOERun {
  run_id: number;
  coded: Record<string, number>;
  natural: Record<string, number>;
  ai_suggested?: boolean;
  // Closed-loop KG chemical-feasibility gate (set when the shared formulation
  // skeleton shares an INHIBITS relation in the knowledge graph).
  infeasible?: boolean;
  infeasible_reason?: string | null;
}

export interface DOEPlan {
  design: string;
  factors: DOEFactor[];
  runs: DOERun[];
  notes: string;
  plan_id: string;
  domain: ProductDomain | null;
}

export interface ExperimentRecord {
  domain: ProductDomain;
  project_id?: string;
  factors: Record<string, number>;
  cure_temperature_c?: number | null;
  measured: Record<string, number>;
  source?: string;
  label?: string;
}

export interface ModelInfo {
  domain: ProductDomain;
  project_id?: string;
  metric: string;
  backend: string;
  n_samples: number;
  r2: number;
  cv_r2: number | null;
  rmse: number;
}

export interface TrainingReport {
  trained: ModelInfo[];
  total_records: number;
  message: string;
}

/** B: 训练数据就绪度 — 数据 < min_samples 时寻优结果是预测器先验。 */
export interface TrainingStatus {
  total_records: number;
  min_samples: number;
  sufficient: boolean;
  models_trained: number;
  by_domain: Record<string, number>;
  message: string;
}

export interface Attachment {
  id: string;
  experiment_id: number;
  source_document_id: string;
  kind: string;
  filename: string;
  note: string;
  created_at: string | null;
}

export interface WorkbenchRow {
  id: number;
  campaign_id: number;
  status: string;
  planned_params: Record<string, number>;
  actual_params: Record<string, number>;
  measurements: Record<string, number | string>;
  // Predicted values for planned-but-unmeasured rows; optional numeric fallback
  // when a metric has not been measured yet.
  predicted?: Record<string, number | string>;
  // Phase 2
  note?: string | null;
  tags?: string[];
  parent_sample_id?: string | null;
  parent_campaign_id?: number | null;
  attachments?: Attachment[];
  // P3: DataLab platform refcode (version history is keyed by it)
  refcode?: string | null;
  // P5: measurements already ingested into training data
  ingested?: boolean;
}

export interface WorkbenchCampaignResponse {
  campaign_id: number;
  name: string;
  strategy: string;
  status: string;
  project_id?: string | null;
  primary_metric?: string | null;
  objectives_snapshot?: ObjectiveSpec[];
  loop_history?: Array<Record<string, unknown>>;
  rows: WorkbenchRow[];
}

export interface WorkbenchCampaignSummary {
  id: number;
  name: string;
  status: string;
  strategy: string;
  row_count: number;
  project_id: string | null;
}

export interface BatchUpdateRequest {
  campaign_id: number;
  rows: Array<{
    id: number;
    status: string;
    actual_params: Record<string, number>;
    measurements: Record<string, number | string>;
  }>;
  trigger_loop?: boolean | null;
  requirement?: Requirement;
  optimize_engine?: string;
  doe_engine?: string;
  campaign_state?: string | null;
}

export interface WorkbenchSyncResponse {
  updated: number;
  rows: WorkbenchRow[];
  training_ingested?: number;
  training_message?: string;
  prediction_bias?: { n_rows: number; by_metric: Record<string, { n: number; mean_error: number; rmse: number; mae: number; max_abs: number }> } | null;
  kg_written?: number | null;
  loop_task_id?: string | null;
  loop_message?: string;
  quality?: { dropped_values: number; dropped: string[] };
}

export interface WorkbenchQuality {
  stale_count: number;
  stale_refs: string[];
  errors_count: number;
  dropped_total: number;
}

export interface ReconcileResult {
  removed: string[];
  kept: Array<{ id: number; item_id: string }>;
  removed_count: number;
  errors: string[];
}

export interface FactorCandidate {
  name: string;
  low: number;
  high: number;
  unit: string;
  rationale: string;
  evidence_ids: string[];
  source: string;
}

export interface KBSourcesResponse {
  sources: Array<{
    id: string;
    title: string;
    filename: string;
    source_kind: string;
    origin_url?: string | null;
    project_id?: string | null;
    raw_text_chars: number;
    extraction_status: string;
  }>;
  total: number;
}

// Objective metric collected per domain (mirrors backend OBJECTIVE map).

// ── Inverse design: target properties -> Pareto set of formulations ─────────

export interface HardConstraint {
  metric: string;
  op: "le" | "ge" | "between";
  value: number;
  value_max?: number | null;
  label?: string;
}

export interface TargetSpec {
  hard: HardConstraint[];
  soft: ObjectiveSpec[];
}

export interface DesignCandidate {
  formulation: Formulation;
  pareto_rank: number | null;
  feasible: boolean;
  violation: number;
  materials: string[];
}

export interface InverseDesignResult {
  topic: string;
  domain: string;
  candidates: DesignCandidate[];
  pareto_frontier_ids: string[];
  tradeoff?: TradeOffAnalysis | null;
  generations: number;
  evaluations: number;
  rejected_infeasible: number;
  seeded_from: Record<string, number>;
  engine: string;
  warnings: string[];
}

// ── Material substitution ──────────────────────────────────────────────────

export interface MetricDelta {
  before: number | null;
  after: number | null;
  delta: number | null;
  pct: number | null;
}

export interface SubstituteCandidate {
  material: string;
  zh_name?: string | null;
  role?: string | null;
  functional_class?: string | null;
  substitute_group?: string | null;
  availability: string;
  supplier?: string | null;
  structural_score: number;
  structural_breakdown: Record<string, number>;
  deltas: Record<string, MetricDelta>;
  /** How much resolution the predicted delta has — see the backend note. */
  delta_confidence: "high" | "low" | "cost_only";
  feasible: boolean;
  blocking_reasons: string[];
  score_after: number | null;
}

export interface SubstitutionReport {
  original: string;
  slot_index: number;
  role: string;
  substitute_group: string | null;
  base_metrics: Record<string, number>;
  candidates: SubstituteCandidate[];
  total_considered: number;
}

export interface SupplyRiskReport {
  at_risk: Record<string, string>;
  affected: {
    formulation: string;
    affected_slots: { slot_index: number; material: string; availability: string }[];
    suggestions: Record<string, { material: string; structural_score: number; feasible: boolean }[]>;
  }[];
}

// ── Experiments and QC reports ─────────────────────────────────────────────

export interface ExperimentSummary {
  id: number;
  domain: string;
  label: string;
  source: string;
  project_id: string;
  measured: Record<string, number>;
  measurement_count: number;
  created_at: string | null;
}

export interface QCMeasurementView {
  metric: string;
  value: number;
  unit: string;
  test_method: string;
  spec_min: number | null;
  spec_max: number | null;
  passed: boolean | null;
}

export interface QCReportResult {
  experiment_id: number;
  source_id: string;
  measurements: QCMeasurementView[];
  measurement_count: number;
  attached: boolean;
  already_attached: boolean;
  synced_measured: Record<string, number>;
  report_meta: Record<string, unknown>;
  parser: string;
  extraction_error: string | null;
  message: string;
}

// ── Formulation revision history ───────────────────────────────────────────

export interface FormulationVersionView {
  id: string;
  lineage_id: string;
  version: number;
  parent_version_id: string | null;
  name: string;
  domain: string;
  change_summary: string;
  created_by: string;
  created_at: string | null;
  // Frozen formulation payload at this revision (graph/tooltip views); absent
  // on older records that predate snapshot storage.
  snapshot?: Record<string, unknown>;
}

export interface IngredientChangeView {
  name: string;
  change: "added" | "removed" | "adjusted";
  role: string;
  before_pct: number | null;
  after_pct: number | null;
  delta_pct: number | null;
}

export interface VersionDiffResult {
  from_version: number;
  to_version: number;
  change_summary: string;
  topology_changed: boolean;
  renamed: string[] | null;
  ingredient_changes: IngredientChangeView[];
  metric_deltas: Record<string, MetricDelta>;
}

export const OBJECTIVE_METRIC: Record<ProductDomain, string> = {
  anticorrosion_coating: "salt_spray_hours",
  degreaser: "cleaning_efficiency",
  surface_treatment: "salt_spray_hours",
};

export function primaryObjectiveMetric(req: Requirement): string {
  if (req.objectives?.length) return req.objectives[0].metric;
  return OBJECTIVE_METRIC[req.domain];
}

/** Normalized API failure for store actions and UI banners. */
export class ApiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function readApiError(res: Response, path: string): Promise<string> {
  let detail = `${path} -> ${res.status}`;
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      detail = body.detail;
    } else if (Array.isArray(body.detail)) {
      detail = body.detail
        .map((item) => (typeof item === "object" && item && "msg" in item ? String((item as { msg: unknown }).msg) : String(item)))
        .join("；");
    }
  } catch {
    // keep status fallback
  }
  return detail;
}

export function formatApiError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

/** Normalize evidence before POST /api/chat (clamp relevance, fill required fields). */
export function sanitizeEvidenceForApi(ev: Evidence): Evidence {
  const rel = Number(ev.relevance);
  const identifier = (ev.identifier || ev.title || "source").trim() || "source";
  const title = (ev.title || identifier).trim() || identifier;
  const snippet = (ev.snippet ?? "").trim();
  return {
    ...ev,
    source: (ev.source || "local").trim() || "local",
    identifier,
    title,
    snippet: snippet || title,
    relevance: Number.isFinite(rel) ? Math.min(1, Math.max(0, rel)) : 0.5,
  };
}

const API_TOKEN_STORAGE_KEY = "formumind-api-token";

export function getApiToken(): string | null {
  const fromEnv = import.meta.env.VITE_API_TOKEN;
  if (typeof fromEnv === "string" && fromEnv.trim()) return fromEnv.trim();
  try {
    const stored = localStorage.getItem(API_TOKEN_STORAGE_KEY);
    return stored?.trim() || null;
  } catch {
    return null;
  }
}

export function setApiToken(token: string): void {
  localStorage.setItem(API_TOKEN_STORAGE_KEY, token.trim());
}

function apiAuthHeaders(): Record<string, string> {
  const token = getApiToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function jsonHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", ...apiAuthHeaders() };
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(await readApiError(res, path));
  return res.json();
}

async function postAccepted(path: string, body: unknown): Promise<AsyncTaskAccepted> {
  const res = await fetch(path, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  if (res.status !== 202) throw new ApiError(await readApiError(res, path));
  return res.json();
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: apiAuthHeaders() });
  if (!res.ok) throw new ApiError(await readApiError(res, path));
  return res.json();
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(await readApiError(res, path));
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: "DELETE", headers: apiAuthHeaders() });
  if (!res.ok) throw new ApiError(await readApiError(res, path));
  return res.json();
}

export interface ProjectDetailResponse {
  id: string;
  title: string;
  headline: string;
  domain: string;
  created_at: string;
  updated_at: string;
  workspace: import("./projectWorkspace").ProjectWorkspacePayload;
}

/** POST /api/kg/formulations/similar — cross-project similar-formulation search. */
export interface SimilarFormulationMatch {
  experiment_id: number;
  project_id: string;
  project_title: string | null;
  similarity: number;
  factors: Record<string, number>;
  measured: Record<string, number>;
  shared_ingredients: string[];
  differing_ingredients: string[];
}

export interface SimilarFormulationResponse {
  matches: SimilarFormulationMatch[];
  query_factors: Record<string, number>;
}

/** GET /api/org/dashboard — organization-level R&D aggregate stats. */
export interface OrgDashboardStats {
  total_experiments: number;
  total_campaigns: number;
  total_projects: number;
  active_projects: number;
  by_domain: Record<string, number>;
  top_performers: Array<{
    metric: string;
    value: number;
    experiment_id: number;
    project_title: string;
    formulation_preview: string;
    measured_at: string;
  }>;
  ingredient_frequency: Array<{
    ingredient_name: string;
    experiment_count: number;
    avg_weight_pct: number;
    best_result_metric: string | null;
  }>;
  convergence_rate: number;
  avg_rounds_to_converge: number;
  recent_activity: { experiments_added: number; campaigns_created: number };
}

export const api = {
  research: (req: Requirement, sources: Evidence[] = [], query = "") =>
    post<ResearchResult>("/api/research", { ...req, sources, query }),
  recommendFormulations: (
    req: Requirement,
    objectives?: ObjectiveSpec[],
    sources: Evidence[] = [],
    n = 3
  ) =>
    post<RecommendFormulationsResponse>("/api/formulations/recommend", {
      requirement: req,
      objectives: objectives ?? req.objectives,
      sources,
      n,
    }),
  chemicalLookup: (q: string) =>
    get<{
      query: string;
      cas: string;
      iupac_name: string;
      zh_name: string;
      formula: string;
      smiles?: string | null;
      molar_mass?: number | null;
      found: boolean;
      source: string;
    }>(`/api/chemical/lookup?q=${encodeURIComponent(q)}`),
  chemicalProfile: (q: string) =>
    get<ChemicalProfile>(`/api/chemical/profile?q=${encodeURIComponent(q)}`),
  chemicalTools: () => get<ChemToolsStatus>("/api/chemical/tools"),
  addManualFormulation: (formulation: Formulation, requirement?: Requirement) =>
    post<{ formulation: Formulation; warnings: string[] }>("/api/formulations/manual", {
      formulation,
      requirement: requirement ?? null,
    }),
  validateFormulations: (formulations: Formulation[], requirement?: Requirement | null) =>
    post<{ formulations: Formulation[]; warnings: string[] }>("/api/formulations/validate", {
      formulations,
      requirement: requirement ?? null,
    }),
  modifyFormulations: (
    req: Requirement,
    modifyPrompt: string,
    opts: {
      sources?: Evidence[];
      baseFormulas?: Formulation[];
      baseFormulation?: Formulation;
      query?: string;
      n?: number;
    } = {}
  ) =>
    postAccepted("/api/research/modify", {
      requirement: req,
      modify_prompt: modifyPrompt,
      sources: opts.sources ?? [],
      base_formulas: opts.baseFormulas ?? (opts.baseFormulation ? [opts.baseFormulation] : []),
      base_formulation: opts.baseFormulation ?? null,
      query: opts.query ?? "",
      n: opts.n ?? 3,
    }),
  doe: (req: Requirement, design: string, engine = "auto") =>
    post<DOEPlan>(`/api/doe?design=${encodeURIComponent(design)}&engine=${encodeURIComponent(engine)}`, req),
  listDoeHistory: (opts: { campaignId?: number | null; page?: number; pageSize?: number } = {}) =>
    get<{ items: Record<string, unknown>[]; total: number; page: number; page_size: number }>(
      `/api/doe/history?page=${opts.page ?? 1}&page_size=${opts.pageSize ?? 20}` +
        (opts.campaignId != null ? `&campaign_id=${opts.campaignId}` : "")
    ),
  suggestFactors: (req: Requirement) =>
    post<{ factors: FactorCandidate[]; count: number }>("/api/doe/suggest-factors", req),
  kbSources: (projectId?: string | null, limit = 100) =>
    get<KBSourcesResponse>(
      `/api/kb/sources?limit=${limit}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}`
    ),
  activeDoe: (
    req: Requirement,
    opts: {
      n_suggest?: number;
      doe_design?: string;
      engine?: string;
      doe_engine?: string;
      campaign_state?: string | null;
      workbench_campaign_id?: number | null;
      existing_records?: ExperimentRecord[];
      budget_remaining?: number | null;
    } = {}
  ) =>
    post<ActiveDoeResult>("/api/doe/active", {
      ...req,
      existing_records: opts.existing_records,
      n_suggest: opts.n_suggest ?? 4,
      doe_design: opts.doe_design ?? "lhs",
      engine: opts.engine ?? "auto",
      doe_engine: opts.doe_engine ?? "auto",
      campaign_state: opts.campaign_state ?? null,
      workbench_campaign_id: opts.workbench_campaign_id ?? null,
      budget_remaining: opts.budget_remaining ?? null,
    }),
  // ── Inverse design ──
  startInverseDesign: (
    req: Requirement,
    targets: TargetSpec,
    opts: { population?: number; generations?: number; seed_with_llm?: boolean } = {}
  ) =>
    postAccepted("/api/design/inverse", {
      requirement: req,
      targets,
      population: opts.population ?? 48,
      generations: opts.generations ?? 30,
      seed_with_llm: opts.seed_with_llm ?? true,
    }),

  // ── Material substitution ──
  findSubstitutes: (body: {
    requirement?: Requirement;
    formulation?: Formulation;
    material?: string;
    slot_index?: number;
    limit?: number;
    include_unavailable?: boolean;
  }) => post<SubstitutionReport>("/api/materials/substitutes", body),

  supplyRisk: () => get<SupplyRiskReport>("/api/materials/supply-risk"),

  setMaterialAvailability: (name: string, availability: string) =>
    post<unknown>("/api/materials/availability", { name, availability }),

  // ── Experiments and QC reports ──
  listExperiments: (opts: { domain?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.domain) params.set("domain", opts.domain);
    params.set("limit", String(opts.limit ?? 100));
    return get<ExperimentSummary[]>(`/api/experiments?${params}`);
  },

  uploadQcReport: async (
    file: File,
    target: number | { experiment_id?: number; campaign_id?: number; row_id?: number },
    opts: { project_id?: string; sync_measured?: boolean } = {}
  ): Promise<QCReportResult> => {
    const body = new FormData();
    body.append("file", file);
    if (typeof target === "number") {
      body.append("experiment_id", String(target));
    } else {
      if (target.experiment_id != null)
        body.append("experiment_id", String(target.experiment_id));
      if (target.campaign_id != null)
        body.append("campaign_id", String(target.campaign_id));
      if (target.row_id != null)
        body.append("row_id", String(target.row_id));
    }
    if (opts.project_id) body.append("project_id", opts.project_id);
    body.append("sync_measured", String(opts.sync_measured ?? true));
    const res = await fetch("/api/qc/report", {
      method: "POST",
      headers: apiAuthHeaders(),
      body,
    });
    if (!res.ok) throw new ApiError(await readApiError(res, "/api/qc/report"));
    return res.json();
  },

  listWorkbenchCampaigns: () =>
    get<WorkbenchCampaignSummary[]>(`/api/experiments/workbench/campaigns`),

  experimentMeasurements: (experimentId: number) =>
    get<{
      experiment_id: number;
      measurements: QCMeasurementView[];
      attachments: { id: string; source_document_id: string; kind: string }[];
    }>(`/api/qc/experiments/${experimentId}/measurements`),

  getWorkbenchRowMeasurements: (campaignId: number, rowId: number) =>
    get<{
      experiment_id: number;
      measurements: QCMeasurementView[];
      attachments: { id: string; source_document_id: string; kind: string }[];
    }>(`/api/qc/workbench/${campaignId}/rows/${rowId}/measurements`),

  uploadAttachment: async (
    file: File,
    experimentId: number,
    opts: { kind?: string; note?: string } = {}
  ): Promise<Attachment> => {
    const body = new FormData();
    body.append("file", file);
    if (opts.kind) body.append("kind", opts.kind);
    if (opts.note) body.append("note", opts.note);
    const res = await fetch(`/api/experiments/${experimentId}/attachments`, {
      method: "POST",
      headers: apiAuthHeaders(),
      body,
    });
    if (!res.ok)
      throw new ApiError(await readApiError(res, "/api/experiments/attachments"));
    return res.json();
  },

  getAttachments: (experimentId: number) =>
    get<Attachment[]>(`/api/experiments/${experimentId}/attachments`),

  getWorkbenchAttachments: (campaignId: number, rowId: number) =>
    get<Attachment[]>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/attachments`
    ),

  workbenchAttachmentDownloadUrl: (
    campaignId: number,
    rowId: number,
    attachmentId: string
  ) =>
    `/api/experiments/workbench/${campaignId}/rows/${rowId}/attachments/${attachmentId}/download`,

  getWorkbenchVersions: (campaignId: number, rowId: number) =>
    get<{
      refcode: string;
      versions: {
        id: string;
        version: number;
        action?: string;
        timestamp?: string;
        creator?: string | null;
      }[];
    }>(`/api/experiments/workbench/${campaignId}/rows/${rowId}/versions`),

  compareWorkbenchVersions: (
    campaignId: number,
    rowId: number,
    v1: string,
    v2: string
  ) =>
    get<{ refcode: string; diff: Record<string, unknown> }>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/versions?compare_v1=${v1}&compare_v2=${v2}`
    ),

  restoreWorkbenchVersion: (
    campaignId: number,
    rowId: number,
    versionId: string
  ) =>
    post<{ restored: boolean; refcode?: string }>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/versions/${versionId}/restore`,
      {}
    ),

  deleteWorkbenchAttachment: (
    campaignId: number,
    rowId: number,
    attachmentId: string
  ) =>
    del<{ deleted: boolean }>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/attachments/${attachmentId}`
    ),

  uploadWorkbenchAttachment: async (
    file: File,
    campaignId: number,
    rowId: number,
    opts: { kind?: string; note?: string } = {}
  ): Promise<Attachment> => {
    const body = new FormData();
    body.append("file", file);
    if (opts.kind) body.append("kind", opts.kind);
    if (opts.note) body.append("note", opts.note);
    const res = await fetch(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/attachments`,
      { method: "POST", headers: apiAuthHeaders(), body }
    );
    if (!res.ok)
      throw new ApiError(
        await readApiError(res, "/api/experiments/workbench/attachments")
      );
    return res.json();
  },

  // ── Formulation revision history ──
  saveFormulationVersion: (body: {
    formulation: Formulation;
    lineage_id?: string | null;
    parent_version_id?: string | null;
    change_summary?: string;
    created_by?: string;
  }) => post<FormulationVersionView>("/api/formulations/versions", body),

  findFormulationLineages: (name: string, domain: string, limit = 10) => {
    const params = new URLSearchParams({ name, domain, limit: String(limit) });
    return get<{ lineage_id: string; versions: FormulationVersionView[] }[]>(
      `/api/formulations/versions?${params}`
    );
  },

  formulationLineage: (lineageId: string) =>
    get<{ lineage_id: string; versions: FormulationVersionView[] }>(
      `/api/formulations/versions/${encodeURIComponent(lineageId)}`
    ),

  diffFormulationVersions: (fromId: string, toId: string) =>
    get<VersionDiffResult>(
      `/api/formulations/versions/${encodeURIComponent(fromId)}/diff/${encodeURIComponent(toId)}`
    ),

  startOptimize: (
    req: Requirement,
    iterations: number,
    engine = "auto",
    campaignState?: string | null,
    workbenchCampaignId?: number | null
  ) =>
    postAccepted("/api/optimize", {
      requirement: req,
      iterations,
      engine,
      campaign_state: campaignState ?? null,
      workbench_campaign_id: workbenchCampaignId ?? null,
    }),

  submitDeepResearch: (
    topic: string,
    req: Requirement,
    sources: Evidence[],
    query = ""
  ) =>
    postAccepted("/api/research/deep", { topic, requirement: req, sources, query }),

  submitRecommendResearch: (req: Requirement, sources: Evidence[] = [], query = "") =>
    postAccepted("/api/research/recommend", { ...req, sources, query }),

  task: async (id: string): Promise<TaskStatus> => {
    const res = await fetch(`/api/tasks/${id}`, { headers: apiAuthHeaders() });
    if (!res.ok) throw new Error(`task ${id} -> ${res.status}`);
    return res.json();
  },

  cancelTask: async (id: string): Promise<TaskStatus> => {
    const res = await fetch(`/api/tasks/${id}/cancel`, { method: "POST", headers: apiAuthHeaders() });
    if (!res.ok) throw new Error(`cancel ${id} -> ${res.status}`);
    return res.json();
  },
  submitExperiments: (records: ExperimentRecord[]) =>
    post<TrainingReport>("/api/experiments", { records, retrain: true }),
  createWorkbenchCampaign: (
    plan: DOEPlan,
    name?: string,
    strategy?: string,
    requirement?: Requirement,
    projectId?: string
  ) =>
    post<WorkbenchCampaignResponse>("/api/experiments/workbench/campaigns", {
      plan,
      name,
      strategy,
      requirement,
      project_id: projectId,
    }),
  getWorkbenchCampaign: (campaignId: number) =>
    get<WorkbenchCampaignResponse>(`/api/experiments/workbench/${campaignId}`),
  getWorkbenchQuality: (campaignId: number) =>
    get<WorkbenchQuality>(`/api/experiments/workbench/${campaignId}/quality`),
  reconcileWorkbench: (campaignId: number) =>
    post<ReconcileResult>(`/api/experiments/workbench/${campaignId}/reconcile`, {}),
  listCampaignRounds: (campaignId: number, opts: { page?: number; pageSize?: number } = {}) =>
    get<{ rounds: Record<string, unknown>[]; total_rounds: number; page: number; page_size: number; unassociated_ledger: number }>(
      `/api/experiments/workbench/${campaignId}/rounds?page=${opts.page ?? 1}&page_size=${opts.pageSize ?? 5}`
    ),

  getRowLineage: (campaignId: number, rowId: number) =>
    get<WorkbenchRow[]>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/lineage`
    ),
  syncWorkbench: (body: BatchUpdateRequest) =>
    put<WorkbenchSyncResponse>("/api/experiments/workbench/sync", body),
  models: () => get<ModelInfo[]>("/api/models"),
  trainingStatus: () => get<TrainingStatus>("/api/training-status"),
  doeExportUrl: (planId: string, format: "csv" | "xlsx" = "csv") =>
    `/api/doe/${planId}/export?format=${format}`,
  importExperimentsCsv: async (file: File, domain?: ProductDomain): Promise<TrainingReport> => {
    const fd = new FormData();
    fd.append("file", file);
    const q = domain ? `?domain=${domain}` : "";
    const res = await fetch(`/api/experiments/import-csv${q}`, {
      method: "POST",
      headers: apiAuthHeaders(),
      body: fd,
    });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        // ignore parse failure, keep status code
      }
      throw new Error(detail);
    }
    return res.json();
  },

  search: (req: SearchRequest) =>
    post<SearchResponse>("/api/search", req),

  searchStream: (req: SearchRequest) => postAccepted("/api/search/stream", req),

  notebooklmStatus: () =>
    get<NotebookLMStatus>("/api/notebooklm/auth-status"),

  notebooklmConfig: (cfg: { enabled?: boolean; notebook_id?: string }) =>
    post<NotebookLMStatus>("/api/notebooklm/config", cfg),

  notebooklmLogin: () =>
    post<NotebookLMLoginResult>("/api/notebooklm/login", {}),

  ingest: async (file: File): Promise<IngestResponse> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/ingest", { method: "POST", headers: apiAuthHeaders(), body: fd });
    if (!res.ok) throw new Error(`/api/ingest -> ${res.status}`);
    return res.json();
  },

  ingestBatch: async (files: File[]): Promise<IngestResponse & { files_processed?: number }> => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    const res = await fetch("/api/ingest/batch", { method: "POST", headers: apiAuthHeaders(), body: fd });
    if (!res.ok) throw new Error(`/api/ingest/batch -> ${res.status}`);
    return res.json();
  },

  ingestUrl: (url: string) =>
    post<IngestResponse>("/api/ingest/url", { url }),

  ingestText: (text: string, title?: string) =>
    post<IngestResponse>("/api/ingest/text", { text, title: title ?? "Pasted text" }),

  listProjects: () => get<import("./projectWorkspace").ProjectSummary[]>("/api/projects"),

  getDefaultLevers: (params: {
    domain: ProductDomain;
    substrate?: string;
    cure_temperature_c?: number | null;
  }) => {
    const q = new URLSearchParams();
    q.set("domain", params.domain);
    if (params.substrate) q.set("substrate", params.substrate);
    if (params.cure_temperature_c != null) {
      q.set("cure_temperature_c", String(params.cure_temperature_c));
    }
    return get<{ levers: LeverSpec[] }>(`/api/meta/default-levers?${q.toString()}`);
  },

  createProject: (title = "", requirement?: Requirement) =>
    post<ProjectDetailResponse>("/api/projects", { title, requirement }),

  getProject: (id: string) => get<ProjectDetailResponse>(`/api/projects/${encodeURIComponent(id)}`),

  updateProject: (id: string, workspace: import("./projectWorkspace").ProjectWorkspacePayload, title?: string) =>
    put<ProjectDetailResponse>(`/api/projects/${encodeURIComponent(id)}`, { workspace, title }),

  deleteProject: (id: string, knowledge: "delete" | "global" = "delete") =>
    del<{ ok: boolean }>(
      `/api/projects/${encodeURIComponent(id)}?knowledge=${knowledge}`
    ),

  getProjectDbStats: (id: string) =>
    get<{
      project_id: string;
      document_count: number;
      campaign_count: number;
      experiment_count: number;
    }>(`/api/projects/${encodeURIComponent(id)}/db-stats`),

  migrateLocalProjects: (snapshots: {
    id: string;
    timestamp: string;
    domain: string;
    headline: string;
    requirement: Requirement;
    leaderboard: Formulation[];
    models: ModelInfo[];
    optimization_history: number[];
  }[]) =>
    post<import("./projectWorkspace").ProjectSummary[]>("/api/projects/migrate-local", { snapshots }),

  chat: (req: ChatRequest) => post<ChatResponse>("/api/chat", req),

  /** SSE 流式问答: 逐事件回调; AbortSignal 可中断(组件卸载/停止)。 */
  chatStream: async (
    req: ChatRequest,
    onEvent: (ev: ChatStreamEvent) => void,
    opts: { signal?: AbortSignal } = {},
  ): Promise<void> => {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(req),
      signal: opts.signal,
    });
    if (!res.ok || !res.body) {
      throw new ApiError(await readApiError(res, "/api/chat/stream"));
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = block
          .split("\n")
          .find((l) => l.startsWith("data: "));
        if (!line) continue; // 心跳注释行等
        try {
          onEvent(JSON.parse(line.slice(6)) as ChatStreamEvent);
        } catch {
          // 单条事件解析失败不中断整个流
        }
      }
    }
  },

  /** 上传结构图 → MolScribe 识别 → SMILES + MolJSON + 相似材料命中。 */
  uploadStructure: async (
    image: File,
    opts: { threshold?: number; top_k?: number } = {}
  ): Promise<StructureRecognitionResult> => {
    const body = new FormData();
    body.append("image", image);
    if (opts.threshold != null) body.append("threshold", String(opts.threshold));
    if (opts.top_k != null) body.append("top_k", String(opts.top_k));
    const res = await fetch("/api/chemical/structure", {
      method: "POST",
      headers: apiAuthHeaders(),
      body,
    });
    if (!res.ok) throw new ApiError(await readApiError(res, "/api/chemical/structure"));
    return res.json();
  },

  kbStats: () => get<KBStats>("/api/kb/stats"),

  kbReindex: () => post<KBReindexResult>("/api/kb/reindex", {}),

  kgResolve: (q: string) =>
    get<KGEntityResolveResponse>(`/api/kg/resolve?q=${encodeURIComponent(q)}`),

  kgRelations: (entityId: string, direction = "both", limit = 20, extraction_method?: string) => {
    const params = new URLSearchParams({ direction, limit: String(limit) });
    if (extraction_method) params.set("extraction_method", extraction_method);
    return get<KGRelationView[]>(
      `/api/kg/relations/${encodeURIComponent(entityId)}?${params}`
    );
  },

  kgFeedbackStats: () =>
    get<{ measured_total: number; measured_performance: number; by_campaign: Record<string, number> }>(
      "/api/kg/feedback/stats"
    ),

  kgFeedbackReport: () =>
    get<{ measured_total: number; measured_performance: number; by_campaign: Record<string, number>; alert: string | null; recent_bias: unknown[] }>(
      "/api/kg/feedback/report"
    ),

  getBiasTrend: (campaignId: number, thresholdRmse = 50) =>
    get<{ campaign_id: number; trend: { at: string | null; n_rows: number; by_metric: Record<string, { n: number; mean_error: number; rmse: number; mae: number; max_abs: number }> }[]; alerts: string[]; threshold_rmse: number }>(
      `/api/experiments/workbench/${campaignId}/bias-trend?threshold_rmse=${thresholdRmse}`
    ),

  kgSubstitutes: (opts: { entityId?: string; q?: string; limit?: number }) => {
    const params = new URLSearchParams();
    if (opts.entityId) params.set("entity_id", opts.entityId);
    if (opts.q) params.set("q", opts.q);
    if (opts.limit) params.set("limit", String(opts.limit));
    return get<KGSubstituteDiscoverResponse>(`/api/kg/discover/substitutes?${params}`);
  },

  kgContradictions: (opts: { entityId?: string; q?: string }) => {
    const params = new URLSearchParams();
    if (opts.entityId) params.set("entity_id", opts.entityId);
    if (opts.q) params.set("q", opts.q);
    return get<KGContradictionResponse>(`/api/kg/contradictions?${params}`);
  },

  kgSimilarFormulations: (factors: Record<string, number>, limit = 10) =>
    post<SimilarFormulationResponse>("/api/kg/formulations/similar", { factors, limit }),

  orgDashboard: () => get<OrgDashboardStats>("/api/org/dashboard"),

  getEnvFlags: () => get<{ flags: EnvFlag[] }>("/api/settings/env-flags"),

  postEnvFlags: (updates: Record<string, boolean>) =>
    post<{ updated: string[]; rejected: string[]; flags: EnvFlag[] }>(
      "/api/settings/env-flags",
      { updates },
    ),

  getFormulationMode: () =>
    get<{ current: string; choices: { value: string; label: string; desc: string }[] }>(
      "/api/settings/formulation-mode",
    ),

  setFormulationMode: (mode: string) =>
    post<{ mode: string; status: string }>("/api/settings/formulation-mode", { mode }),

  postDoeCyclePause: (campaignId: number | string, isPaused: boolean) =>
    post<{ status: string; message: string }>(
      `/api/experiments/hooks/pause-doecycle/${campaignId}`,
      { isPaused },
    ),

  getOcsr: () => get<{ status: OcsrStatus }>("/api/settings/ocsr"),

  getSettings: () => get<LLMSettingsResponse>("/api/settings"),

  getAuthStatus: () =>
    get<{ auth_required: boolean; hint: string; multi_user?: boolean; owner?: string }>("/api/auth/status"),

  postSettings: (update: Partial<LLMConfig> & { api_key?: string }) =>
    post<{ ok: boolean; provider: string; model: string; message: string }>(
      "/api/settings",
      {
        provider: update.provider,
        model: update.model,
        api_key: update.api_key,
        base_url: update.baseUrl,
      }
    ),

  postVisionSettings: (update: VisionSettingsUpdate) =>
    post<{ ok: boolean } & VisionSettings>("/api/settings/vision", {
      provider: update.provider,
      model: update.model,
      api_key: update.api_key,
      base_url: update.baseUrl,
    }),

  /** Send a real image down the real path — the only honest capability check. */
  testVision: () => post<VisionProbeResult>("/api/settings/vision/test", {}),

  refreshLlmModels: (opts?: { provider?: string; baseUrl?: string; model?: string }) =>
    post<LlmModelsRefreshResponse>("/api/settings/models/refresh", {
      provider: opts?.provider,
      baseUrl: opts?.baseUrl,
      model: opts?.model,
    }),

  testConnection: () =>
    post<{ ok: boolean; provider: string; model: string; message: string }>(
      "/api/settings/test", {}
    ),

  getSecrets: () => get<SecretsListResponse>("/api/settings/secrets"),

  postSecrets: (updates: Record<string, string>) =>
    post<SecretsListResponse>("/api/settings/secrets", { updates }),

  testSecret: (id: string) =>
    post<{ ok: boolean; message: string }>("/api/settings/secrets/test", { id }),

  analyzeIP: (req: IPAnalysisRequest) =>
    post<IPReport>("/api/ip/analyze", req),

  loopIterate: (
    req: Requirement,
    optimize_iterations = 24,
    n_suggest = 4,
    optimize_engine = "auto",
    doe_engine = "auto",
    opts: {
      workbench_campaign_id?: number | null;
      campaign_state?: string | null;
      prior_rmse_history?: Record<string, number>[];
      prior_optimization?: OptimizationResult | null;
      prior_next_doe?: DOEPlan | null;
      budget_remaining?: number | null;
    } = {}
  ) =>
    postAccepted("/api/loop/iterate", {
      ...req,
      optimize_iterations,
      n_suggest,
      optimize_engine,
      doe_engine,
      workbench_campaign_id: opts.workbench_campaign_id ?? null,
      campaign_state: opts.campaign_state ?? null,
      prior_rmse_history: opts.prior_rmse_history ?? [],
      prior_optimization: opts.prior_optimization ?? null,
      prior_next_doe: opts.prior_next_doe ?? null,
      budget_remaining: opts.budget_remaining ?? null,
    }),

  parseIntent: (text: string) =>
    post<IntentResult>("/api/intent/parse", { text }),

  loadExampleProject: (exampleId: string) =>
    get<Requirement>(`/api/examples/${encodeURIComponent(exampleId)}`),

  getSourceStatus: () =>
    get<Record<string, SourceStatus>>("/api/search/status"),

  getRagStatus: () =>
    get<{ backend: string; formulation_mode: string; gpu_enabled: boolean; gpu_available: boolean; rag_backend_setting: string; prewarm: { status: string; backend: string | null; elapsed_ms: number | null; error: string | null } }>("/api/research/rag/status"),

  prewarmRag: (background = true) =>
    post<{ status: string; backend: string | null; elapsed_ms: number | null; error: string | null }>(`/api/research/rag/prewarm?background=${String(background)}`, {}),

  listDependencies: () =>
    get<DependencyListResponse>("/api/dependencies"),

  installDependencies: (names: string[], upgrade = false) =>
    postAccepted("/api/dependencies/install", { names, upgrade }),
};

const TASK_STATE_MAP: Record<TaskProgressStatus, TaskStatus["state"]> = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELLED: "cancelled",
};

/** Map SSE progress event to legacy TaskStatus snapshot shape. */
export function progressToTaskStatus(
  taskId: string,
  kind: string,
  ev: TaskProgressEvent
): TaskStatus {
  return {
    task_id: taskId,
    kind,
    state: TASK_STATE_MAP[ev.status],
    progress: ev.progress ?? 0,
    message: ev.message,
    result: ev.data ?? null,
    stream_url: `/api/tasks/${taskId}/stream`,
    stage: (ev as any).stage ?? "",
    elapsed_ms: (ev as any).elapsed_ms ?? null,
  };
}

function streamUrl(path: string): string {
  const token = getApiToken();
  if (!token) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}token=${encodeURIComponent(token)}`;
}

/** Subscribe to task SSE progress (GET /api/tasks/{id}/stream). */
export function subscribeTaskStream(
  taskId: string,
  onEvent: (ev: TaskProgressEvent) => void,
  onError?: (err: Event) => void
): EventSource {
  const es = new EventSource(streamUrl(`/api/tasks/${taskId}/stream`));
  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data) as TaskProgressEvent);
    } catch {
      // ignore malformed frames
    }
  };
  es.onerror = onError ?? (() => es.close());
  return es;
}

/** Await task completion via EventSource; resolves with terminal COMPLETED event. */
/**
 * Wait for a background task, streaming its progress.
 *
 * `timeoutMs = 0` disables the wall-clock limit, for jobs whose duration is
 * genuinely unbounded — building a knowledge base from several hundred documents
 * takes as long as the downloads take, and cutting it off at a fixed number is
 * arbitrary. Note that this timeout only ever stopped the *client* watching; the
 * server-side task carries on regardless, which is why the old copy claiming the
 * build had been "interrupted" was wrong.
 *
 * `idleTimeoutMs` is what makes an unlimited wait safe. It resets on every
 * progress event, so a slow job never trips it, but a worker that has died — OOM
 * killed, container restarted — stops emitting and is reported instead of
 * spinning forever. Total duration unlimited, silence bounded.
 */
export function awaitTaskStream(
  taskId: string,
  onEvent?: (ev: TaskProgressEvent) => void,
  timeoutMs = 120_000,
  signal?: AbortSignal,
  idleTimeoutMs = 0
): Promise<TaskProgressEvent> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let es: EventSource;
    let idleTimer: ReturnType<typeof setTimeout> | null = null;

    const clearIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = null;
    };

    const armIdle = () => {
      if (idleTimeoutMs <= 0) return;
      clearIdle();
      idleTimer = setTimeout(() => {
        es?.close();
        finish(() =>
          reject(
            new Error(
              `已 ${Math.round(idleTimeoutMs / 1000)}s 没有进度更新 — 任务可能已中止（请查看服务端日志）`
            )
          )
        );
      }, idleTimeoutMs);
    };

    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      clearIdle();
      signal?.removeEventListener("abort", onAbort);
      fn();
    };

    const onAbort = () => {
      es?.close();
      finish(() => reject(new Error("任务已取消")));
    };

    const resolveFromStatus = (s: TaskStatus) => {
      const map: Record<string, TaskProgressStatus> = { completed: "COMPLETED", failed: "FAILED", cancelled: "CANCELLED", pending: "PENDING", running: "RUNNING" };
      const ev: TaskProgressEvent = {
        status: (map[s.state] as TaskProgressStatus) || "FAILED",
        message: s.message,
        progress: s.progress,
        stage: s.stage || "",
        data: s.result ?? undefined,
      };
      onEvent?.(ev);
      if (s.state === "completed") {
        finish(() => resolve(ev));
      } else if (s.state === "cancelled") {
        finish(() => reject(new Error(s.message || "任务已取消")));
      } else {
        finish(() => reject(new Error(s.message || "任务失败")));
      }
    };

    const timer =
      timeoutMs > 0
        ? setTimeout(() => {
            es?.close();
            finish(() => reject(new Error(`任务超时（${Math.round(timeoutMs / 1000)}s）`)));
          }, timeoutMs)
        : null;

    signal?.addEventListener("abort", onAbort, { once: true });
    armIdle();

    es = subscribeTaskStream(
      taskId,
      (ev) => {
        armIdle();  // progress means alive — restart the silence clock
        onEvent?.(ev);
        if (ev.status === "COMPLETED" || ev.status === "FAILED" || ev.status === "CANCELLED") {
          es.close();
          if (ev.status === "FAILED" || ev.status === "CANCELLED") {
            finish(() => reject(new Error(ev.message || (ev.status === "CANCELLED" ? "任务已取消" : "任务失败"))));
          } else {
            finish(() => resolve(ev));
          }
        }
      },
      () => {
        es.close();
        // An unlimited wall clock has to mean the fallback is unlimited too,
        // otherwise a dropped SSE connection reintroduces a 120 s ceiling by the
        // back door — which is exactly how a long job "times out" while healthy.
        pollTask(
          taskId,
          (s) => {
            if (settled) return;  // 已 settle：停止向 onEvent 泄漏事件
            armIdle();
            if (s.state === "running" || s.state === "pending") {
              onEvent?.({
                status: s.state === "running" ? "RUNNING" : "PENDING",
                message: s.message,
                progress: s.progress,
              });
            }
          },
          400,
          timeoutMs > 0 ? undefined : 0,
        )
          .then((s) => {
            if (settled) return;  // 超时/取消已先 settle：丢弃迟到的轮询结果
            resolveFromStatus(s);
          })
          .catch(() => {
            finish(() =>
              reject(
                new Error(
                  "SSE 连接中断 — 无法获取任务进度（请检查后端服务；若未启动 Redis，请确认后端已升级支持无 Redis 降级）"
                )
              )
            );
          });
      }
    );
  });
}

// ── v0.3 新增类型 ────────────────────────────────────────────────────────────

export type SearchSourceType = "patents" | "literature" | "internet" | "local" | "notebooklm";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  citations?: Evidence[];
  /** Persistent-KB chunks that grounded this assistant answer. */
  kbChunksUsed?: number;
  /** SSE 流式问答中: 该条 assistant 消息仍在接收(逐 token 累积)。 */
  streaming?: boolean;
  /** SSE 阶段指示: retrieval | answering | claims(仅 streaming 时有意义)。 */
  phase?: string;
}

/** /api/chat/stream 的 SSE 事件(后端 data: JSON 一行一个)。 */
export type ChatStreamEvent =
  | { type: "phase"; phase: "retrieval" | "answering" | "claims" }
  | {
      type: "meta";
      kb_used: number;
      rewritten_query?: string | null;
      source_count?: number;
    }
  | { type: "token"; delta: string }
  | {
      type: "done";
      answer: string;
      citations?: Evidence[];
      kb_chunks_used?: number;
      clarification?: unknown;
      rewritten_query?: string | null;
      sourced_claims?: unknown;
      structured?: unknown;
    }
  | { type: "error"; message: string };

export interface LLMModelOption {
  id: string;
  label: string;
  recommended?: boolean;
}

export interface LLMProviderInfo {
  id: string;
  label: string;
  /** null for a bring-your-own endpoint, which has no default to offer. */
  base_url?: string | null;
  models: LLMModelOption[];
}

export interface LLMConfig {
  provider: string;
  model: string;
  baseUrl?: string;
}

export interface SecretStatus {
  id: string;
  env_key: string;
  label: string;
  group: string;
  set: boolean;
  masked: string;
}

export interface SecretsListResponse {
  secrets: SecretStatus[];
  updated?: string[];
}

export interface SearchRequest {
  query?: string;
  source_types?: SearchSourceType[];
  requirement?: Requirement;
  limit_per_source?: number;
  total_limit?: number;
}

export interface NotebookLMStatus {
  available: boolean;
  reason?: string | null;
  hint?: string | null;
  lib_installed?: boolean;
  enabled?: boolean;
  notebook_id_set?: boolean;
  notebook_id?: string | null;
  session_present?: boolean;
  can_launch_browser?: boolean;
}

export interface NotebookLMLoginResult {
  started: boolean;
  mode: "browser" | "manual";
  reason?: string | null;
  hint?: string | null;
  command?: string | null;
  manual_url?: string | null;
}

export interface SourceStatus {
  available: boolean;
  offline_fallback?: boolean;
  reason?: string | null;
  hint?: string | null;
}

export interface SearchResponse {
  evidence: Evidence[];
  total: number;
  source_status?: Record<string, SourceStatus>;
  used_seed_fallback?: boolean;
  filter_report?: FilterReport | null;
}

/** Aggregated content-filter outcome from search (rule tier + optional LLM judge). */
export interface FilterReport {
  kept: number;
  dropped: number;
  dropped_by_reason: Record<string, number>;
  dropped_examples: string[];
}

/** Incremental search progress payload (SSE task data). */
export interface SearchStreamProgress {
  message: string;
  total: number;
  source: string | null;
  newCount: number;
  sourcesDone: string[];
  sourcesPending: string[];
}

export function parseSearchStreamData(
  data: Record<string, unknown> | null | undefined
): {
  evidence: Evidence[];
  progress: Partial<SearchStreamProgress>;
  usedSeedFallback: boolean;
  filterReport: FilterReport | null;
} {
  if (!data) {
    return { evidence: [], progress: {}, usedSeedFallback: false, filterReport: null };
  }
  const evidence = Array.isArray(data.evidence) ? (data.evidence as Evidence[]) : [];
  const usedSeedFallback =
    data.used_seed_fallback === true || evidence.some((e) => e.is_seed_corpus);
  const rawReport = data.filter_report;
  const filterReport =
    rawReport && typeof rawReport === "object" && !Array.isArray(rawReport)
      ? (rawReport as FilterReport)
      : null;
  return {
    evidence,
    usedSeedFallback,
    filterReport,
    progress: {
      total: typeof data.total === "number" ? data.total : evidence.length,
      source: typeof data.source === "string" ? data.source : null,
      newCount: typeof data.new_count === "number" ? data.new_count : 0,
      sourcesDone: Array.isArray(data.sources_done) ? (data.sources_done as string[]) : [],
      sourcesPending: Array.isArray(data.sources_pending) ? (data.sources_pending as string[]) : [],
    },
  };
}

/** Per-document status of the background KB ingest task (SSE data.docs). */
export interface KbIngestDoc {
  identifier: string;
  title: string;
  kind: string;
  status: "queued" | "fetching" | "indexing" | "indexed" | "skipped" | "failed" | "unsupported";
  source_id?: string | null;
  error?: string | null;
}

export interface KbIngestProgress {
  docs: KbIngestDoc[];
  done: number;
  total: number;
  indexed: number;
  failed: number;
}

export function parseKbIngestData(
  data: Record<string, unknown> | null | undefined
): KbIngestProgress | null {
  if (!data || !Array.isArray(data.docs)) return null;
  const docs = data.docs as KbIngestDoc[];
  return {
    docs,
    done: typeof data.done === "number" ? data.done : 0,
    total: typeof data.total === "number" ? data.total : docs.length,
    indexed:
      typeof data.indexed === "number"
        ? data.indexed
        : docs.filter((d) => d.status === "indexed").length,
    failed:
      typeof data.failed === "number"
        ? data.failed
        : docs.filter((d) => d.status === "failed").length,
  };
}

export interface IngestResponse {
  filename: string;
  evidence: Evidence[];
  total: number;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  citations?: Evidence[];
}

export interface ClarifiedEntity {
  term: string;
  resolved: string;
  entity_id?: string;
}

export type ChatResponseFormat = "markdown" | "structured";

export interface FormulationHint {
  ingredient: string;
  role?: string;
  typical_range?: string;
  evidence_ref: string;
}

export interface StructuredAnswer {
  summary: string;
  key_findings?: string[];
  formulation_hints?: FormulationHint[];
  data_conflicts?: string[];
  uncertainty_notes?: string[];
  assumptions?: string[];
}

export interface ClarificationOption {
  ambiguous_term: string;
  possible_meanings: string[];
  question: string;
  candidate_entity_ids?: string[];
}

export interface SourcedClaim {
  text: string;
  chunk_ids: string[];
  confidence: number;
  status: "supported" | "weak" | "unsupported";
}

export interface ChatRequest {
  question: string;
  sources?: Evidence[];
  domain?: string;
  project_id?: string;
  include_entity_resolution?: boolean;
  history?: ChatTurn[];
  clarified_entities?: ClarifiedEntity[];
  response_format?: ChatResponseFormat;
  attachment_source_ids?: string[];
  /** 结构图识别结果（uploadStructure 返回），相似材料名注入检索。 */
  structure?: StructureRecognitionResult | null;
}

/** POST /api/chemical/structure 返回：图 → SMILES + MolJSON + 相似材料。 */
export interface StructureRecognitionResult {
  recognized: boolean;
  smiles: string | null;
  moljson: { atoms?: unknown[]; bonds?: unknown[] } | null;
  hits: StructureHit[];
  kg_hits?: StructureHit[];
  /** MolScribe overall_score [0,1]，低置信（<0.6）提示人工复核。 */
  confidence?: number | null;
  image_sha: string;
  cached: boolean;
  warnings: string[];
  error: string | null;
}

export interface StructureHit {
  name: string;
  role: string;
  smiles?: string | null;
  similarity: number;
}

export interface ChatResponse {
  answer: string;
  citations: Evidence[];
  rag_backend?: string;
  kb_chunks_used?: number;
  entity_resolution?: KGEntityResolutionSummary | null;
  kg_retrieval_stats?: object | null;
  structured?: StructuredAnswer | null;
  clarification?: ClarificationOption | null;
  rewritten_query?: string | null;
  sourced_claims?: SourcedClaim[] | null;
}

export type KGRelationType =
  | "substitutes"
  | "synergizes"
  | "inhibits"
  | "correlates_pos"
  | "correlates_neg"
  | "requires";

export interface KGRelationEvidence {
  source_id: string;
  chunk_id?: string | null;
  sentence: string;
  confidence: number;
  extraction_method: string;
}

export interface KGRelationView {
  id: string;
  source_entity_id: string;
  target_entity_id: string;
  relation_type: KGRelationType;
  confidence: number;
  evidence: KGRelationEvidence[];
  metadata: Record<string, unknown>;
  is_valid: boolean;
  extraction_method: string;
}

export interface KGChemicalEntity {
  id: string;
  canonical_name: string;
  cas_no?: string | null;
  formula?: string | null;
  linked_catalog_key?: string | null;
  composition_status: string;
  mention_count: number;
}

export interface KGTradeProductEntity {
  id: string;
  trade_name: string;
  grade: string;
  supplier: string;
  composition_status: string;
  proprietary: boolean;
  generic_name_hint: string;
  linked_chemical_ids: string[];
  mention_count: number;
}

export interface KGEntityResolveResponse {
  query: string;
  chemicals: KGChemicalEntity[];
  trade_products: KGTradeProductEntity[];
  expanded_entity_ids: string[];
  top_relations: KGRelationView[];
  mode: string;
  trade_only: boolean;
  interpretation: string;
}

export interface KGEntityResolutionSummary {
  query: string;
  chemicals: KGChemicalEntity[];
  trade_products: KGTradeProductEntity[];
  top_relations: KGRelationView[];
  mode: string;
  truncated: boolean;
}

export interface KGSubstituteCandidate {
  entity_id: string;
  entity_name: string;
  relation_type: KGRelationType;
  confidence: number;
  hops: number;
  path: { relation: KGRelationView; entity_id: string; entity_name: string }[];
  contradiction_flag?: boolean;
  contradiction_detail?: string;
}

export interface KGContradictionMark {
  target_entity_id: string;
  target_entity_name?: string;
  literature_relation: KGRelationType;
  literature_confidence?: number;
  measured_property?: string;
  measured_value?: number | null;
  measured_source_id?: string;
  contradiction_type?: string;
  strength?: number;
  recommended_action?: string;
}

export interface KGContradictionResponse {
  entity_id: string;
  entity_name?: string;
  contradictions: KGContradictionMark[];
}

export interface KGSubstituteDiscoverResponse {
  query_entity_id: string;
  query_entity_name: string;
  substitutes: KGSubstituteCandidate[];
}

/** Persistent knowledge base counters (GET /api/kb/stats). */
export interface KBStats {
  enabled: boolean;
  sources: number;
  sources_by_kind: Record<string, number>;
  chunks: number;
  embedded_chunks: number;
  /** Import-only probe: the library is present, NOT that anything got embedded. */
  embedding_available: boolean;
  /**
   * Whether retrieval really is vector-based. `degraded` is the one to warn
   * about — library installed, zero vectors, so it looks healthy while
   * retrieval has silently fallen back to keyword overlap.
   */
  vector_mode?: "semantic" | "degraded" | "keyword" | "empty";
  vector_hint?: string;
  /** The backend actually in effect, not the configured value. */
  rag_backend?: string;
  products?: number;
}

/** Boolean feature flag backed by a FORMUMIND_* environment variable. */
export interface EnvFlag {
  attr: string;
  env_key: string;
  label: string;
  description: string;
  category: string;
  category_label: string;
  hint: string;
  value: boolean;
  default: boolean;
}

export interface OcsrStatus {
  enabled: boolean;
  molscribe_installed: boolean;
  molscribe_queue: string;
  molscribe_timeout_s: number;
}

export interface KBReindexResult {
  reindexed_sources: number;
  reindexed_chunks: number;
  total_chunks: number;
  embedded_chunks: number;
}

/**
 * The vision role. `provider: ""` means "follow the text model", which is the
 * default and what every install did before roles existed.
 *
 * `configured` says a key and model are present — deliberately NOT that the
 * model can read a picture. For a rented endpoint that is unknowable from the
 * server, so `POST /api/settings/vision/test` is the only way to find out.
 */
export interface VisionSettings {
  provider: string;
  model: string;
  base_url?: string | null;
  key_set: boolean;
  inherits: boolean;
  configured: boolean;
  hint: string;
}

export interface VisionSettingsUpdate {
  provider?: string;
  model?: string;
  api_key?: string;
  baseUrl?: string;
}

export interface VisionProbeResult {
  ok: boolean;
  provider?: string;
  model?: string;
  base_url?: string | null;
  inherits?: boolean;
  message: string;
}

export interface LLMSettingsResponse {
  provider: string;
  model: string;
  key_set: boolean;
  base_url?: string;
  providers: LLMProviderInfo[];
  vision: VisionSettings;
}

export interface LlmModelsRefreshResponse {
  ok: boolean;
  provider: string;
  base_url?: string | null;
  source: "remote" | "static";
  models: LLMModelOption[];
  message: string;
}

// ── v0.5 新增类型 ────────────────────────────────────────────────────────────

export interface PatentRisk {
  patent_id: string;
  title: string;
  risk: "high" | "medium" | "low" | "unknown";
  claim_overlap: string;
  recommendation: string;
}

export interface MoleculePatentCheck {
  name: string;
  smiles: string;
  patented: boolean | null;
}

export interface IPReport {
  formulation_name: string;
  novelty_score: number;
  risks: PatentRisk[];
  whitespace_hints: string[];
  raw_patents_searched: number;
  engine: string;
  molecule_checks?: MoleculePatentCheck[];
}

export interface IPAnalysisRequest {
  formulation: Formulation;
  limit_patents?: number;
}

// ── v0.6 新增类型 ────────────────────────────────────────────────────────────

export interface LoopReport extends AdaptiveDOEMetadata {
  domain: string;
  total_records: number;
  model_info: ModelInfo[];
  rmse_by_metric: Record<string, number>;
  optimization: OptimizationResult;
  next_doe: DOEPlan;
  engine: string;
  campaign_state?: string | null;
  converged?: boolean;
  loop_message?: string;
  // KG chemical-compatibility verdict for the recommended batch's skeleton.
  chemical_feasibility?: ChemicalFeasibility | null;
  // v11: deterministic physical-constraint verdict (acid stability + compliance).
  physical_constraints?: PhysicalConstraints | null;
  // 成本/碳足迹摘要（top 配方均值）
  cost_summary?: { cost_cny_per_kg?: number | null; voc_gpl?: number | null; n: number } | null;
}

export interface IntentResult {
  requirement: Requirement;
  confidence: number;
  extracted_fields: string[];
  engine: string;
  /** Advisory notices, e.g. controlled-chemical hits on parsed materials. */
  warnings?: string[];
}

export interface ComprehensiveReport {
  topic: string;
  report_markdown: string;
  citations: Evidence[];
  candidates: Formulation[];
  web_count: number;
  kb_count: number;
  engine: string;
}

// ── Dependency management ────────────────────────────────────────────────────

export interface DependencyInfo {
  pip_name: string;
  import_name: string;
  extra: string;
  enables: string;
  installed: boolean;
  version: string | null;
}

export interface DependencyListResponse {
  dependencies: DependencyInfo[];
  online_core_missing: string[];
}

export interface DependencyInstallResult {
  ok: boolean;
  returncode?: number;
  summary: string;
  stdout?: string;
  stderr?: string;
}

/** Legacy poll fallback — prefer awaitTaskStream / subscribeTaskStream. */
export async function pollTask(
  id: string,
  onUpdate: (s: TaskStatus) => void,
  intervalMs = 400,
  /** 0 = poll until the task finishes, for jobs with no meaningful deadline. */
  maxAttempts = 300
): Promise<TaskStatus> {
  let consecutiveFailures = 0;
  for (let attempt = 0; !maxAttempts || attempt < maxAttempts; attempt++) {
    let s: TaskStatus;
    try {
      s = await api.task(id);
      consecutiveFailures = 0;
    } catch (e) {
      consecutiveFailures += 1;
      // A transient failure (dev-server reload, a network blip) must not kill
      // the progress tracking of a multi-hour ingest. The task state lives in
      // Redis/disk and is still there once the backend comes back, so retry —
      // give up only after repeated consecutive failures.
      if (consecutiveFailures >= 5) throw e;
      await new Promise((r) => setTimeout(r, intervalMs * 5));
      continue;
    }
    onUpdate(s);
    if (s.state === "completed" || s.state === "failed") return s;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`任务轮询超时（${maxAttempts} 次）`);
}
