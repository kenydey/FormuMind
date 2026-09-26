// Shared API types (P2 split from api.ts).
// Typed backend client. Mirrors the FastAPI domain schemas.

export type ProductDomain =
  | "anticorrosion_coating"
  | "degreaser"
  | "surface_treatment"
  | "autodeposition_coating";

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
  // ── 材料库字段(2026-09-05, 对齐后端 /api/materials upsert) ──
  formula?: string | null;
  cas_no?: string | null;
  zh_name?: string | null;
  molar_mass?: number | null;
  price_cny_per_kg?: number | null;
  voc_contrib?: number | null;
  density_gcm3?: number | null;
  oil_absorption?: number | null;
  tg_k?: number | null;
  suppliers_json?: Supplier[] | null;
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
  found?: boolean;
  source?: string;
  providers_tried?: string[];
  surechembl?: {
    chemical_id?: string | null;
    global_frequency?: number | null;
    inchi_key?: string | null;
    source_url?: string | null;
    alternates?: Array<{
      chemical_id?: string | null;
      name?: string | null;
      smiles?: string | null;
      global_frequency?: number | null;
    }>;
  };
  /** Structured supplier list harvested from PubChem ``Chemical Vendors``.
   *  Only name / homepage / product page — PubChem exposes no commercial terms. */
  suppliers?: Array<{
    name: string;
    url?: string | null;
    product_url?: string | null;
  }>;
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
  // Stable client-side identity for DOE baseline badge matching (not persisted
  // by the backend schema; stamped when saving a card as DOE baseline).
  client_uid?: string;
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
    measured_metric_hits?: {
      material: string;
      metric: string;
      quality: "good" | "poor" | "presence" | string;
      value?: number | null;
      confidence?: number;
      prop_id?: string;
    }[];
    reasons: string[];
  } | null;
  /** Top-5‴ #2: metrics soft-corrected by prediction_bias.mean_error. */
  bias_corrected_metrics?: string[];
  /** Batch C: structured 「为何推荐」 (optional on older rows). */
  explain?: FormulationExplain | null;
}

export interface FormulationExplain {
  objectives_hit?: string[];
  constraints_miss?: string[];
  evidence_refs?: { source_type: string; source_id: string }[];
  kg_signals?: {
    feasible?: boolean;
    status?: string;
    measured_materials?: string[];
    measured_metric_hits?: unknown[];
    inhibits?: string[];
    synergizes?: string[];
  };
  supply_flags?: string[];
  uncertainty?: string[];
  bias_corrected?: boolean;
  bias_corrected_metrics?: string[];
  notes?: string[];
  /** Post-A′ #2: Requirement field wiring audit. */
  effect_trace?: Array<{
    field: string;
    kind: string;
    label: string;
    status: "wired" | "display_only" | "unwired" | string;
    consumers?: string[];
    detail?: string;
  }>;
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
  /** Landing URL (e.g. Google Patents). */
  url?: string | null;
  /** Secondary URL (e.g. SureChEMBL document page). */
  url_alt?: string | null;
  /** SureChEMBL / patent assignee (P3 KG). */
  assignee?: string | null;
  /** Publication date string from SureChEMBL (P3 KG). */
  pub_date?: string | null;
  /** Open Access PDF hint for literature fulltext ingest (P3.2). */
  oa_pdf_url?: string | null;
  is_oa?: boolean | null;
  domain_tags?: string[];
  domain_match?: "strong" | "weak" | "none" | null;
  taxonomy_source?: string | null;
}

/** SureChEMBL P3 / P3.1 embodiment draft (review gate; never auto-promote). */
export interface EmbodimentDraft {
  status: string;
  needs_review: boolean;
  origin: "surechembl" | "surechembl+fulltext" | "patent_fulltext" | "literature_fulltext" | "oa_pdf" | string;
  doc_id: string;
  source_id?: string | null;
  title?: string | null;
  assignee?: string | null;
  pub_date?: string | null;
  url?: string | null;
  url_alt?: string | null;
  amount_source?: "table" | "prose" | "placeholder" | "mixed" | string;
  embodiments?: {
    label: string;
    page_hint?: number | null;
    ingredients: {
      name: string;
      role: string;
      weight_pct: number;
      unit_raw?: string | null;
      confidence?: number;
    }[];
    amount_source?: string;
    warnings?: string[];
  }[];
  formulation: {
    name: string;
    domain: string;
    ingredients: {
      name: string;
      role: string;
      weight_pct: number;
      smiles?: string | null;
      cas_no?: string | null;
    }[];
    predicted?: Record<string, unknown>;
    warnings?: string[];
    source?: string;
  };
  ingredients_detail?: {
    name: string;
    role: string;
    weight_pct: number;
    smiles?: string | null;
    cas_no?: string | null;
    chemical_id?: string | null;
    formula?: string | null;
    global_frequency?: number | null;
    unit_raw?: string | null;
    confidence?: number;
  }[];
  chemistry_count?: number;
}

/** @deprecated alias — prefer EmbodimentDraft */
export type SurechemblExampleDraft = EmbodimentDraft;

export interface EmbodimentEligibilityItem {
  source_id: string;
  eligible: boolean;
  reason?: string | null;
  raw_text_chars?: number;
  extraction_status?: string | null;
  chunk_count?: number;
  source_kind?: string | null;
  has_table_signal?: boolean;
  title?: string | null;
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
  relation_insights?: Array<{
    component?: string;
    cas_no?: string | null;
    substitutes?: string[];
    relations?: Array<{ type?: string; target?: string; confidence?: number }>;
  }>;
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
  /** predictor_virtual = in-loop surrogate scores (not lab); lab | skipped */
  measurement_source?: string;
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

/** Dim-2 thinking timeline step (carried in TaskProgressEvent.data.thinking). */
export type ThinkingStepKind = "stage" | "thought" | "tool";
export type ThinkingStepStatus = "pending" | "running" | "done" | "error";

export interface ThinkingStep {
  id: string;
  kind?: ThinkingStepKind;
  title: string;
  detail?: string;
  status?: ThinkingStepStatus;
}

export interface TaskProgressEvent {
  status: TaskProgressStatus;
  stage?: string;
  message: string;
  progress?: number;
  data?: Record<string, unknown> & { thinking?: ThinkingStep[] };
  elapsed_ms?: number | null;
}

/** Extract thinking steps from a progress event (full snapshot). */
export function extractThinkingSteps(ev: TaskProgressEvent | null | undefined): ThinkingStep[] {
  const raw = ev?.data?.thinking;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((s): s is ThinkingStep => !!s && typeof s === "object" && typeof (s as ThinkingStep).id === "string")
    .map((s) => ({
      id: s.id,
      kind: s.kind,
      title: s.title || s.id,
      detail: s.detail || "",
      status: s.status || "running",
    }));
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
  /** P1 #20: ISO-8601 UTC when this artifact was trained. */
  trained_at?: string | null;
  data_hash?: string | null;
  feature_version?: string | null;
  version_id?: string | null;
}

export interface ModelVersionMeta {
  version_id: string;
  path?: string;
  is_current?: boolean;
  data_hash?: string;
  feature_version?: string;
  trained_at?: string;
  backend?: string;
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
  /** Batch B: idle|running|converged|paused|failed */
  loop_status?: {
    status?: string;
    rounds?: number;
    converged?: boolean;
    paused?: boolean;
    message?: string;
    last_rmse_by_metric?: Record<string, number>;
    last_error?: string | null;
    doe_plan_id?: string | null;
  } | null;
  rows: WorkbenchRow[];
}

export interface WorkbenchCampaignSummary {
  id: number;
  name: string;
  status: string;
  strategy: string;
  row_count: number;
  project_id: string | null;
  /** P1: linked DataLab collection id when ELN backend is active. */
  datalab_collection_id?: string | null;
}

/** GET /api/projects/{id}/history — payload version audit metadata. */
export interface ProjectPayloadVersion {
  version: number;
  cause: string;
  created_at: string | null;
  fields: string[];
  chat_count: number;
  source_count: number;
}

export interface ProjectPayloadHistoryResponse {
  project_id: string;
  versions: ProjectPayloadVersion[];
}

/** Dim-4: project export shelf file metadata. */
export interface ProjectExportFile {
  name: string;
  size: number;
  updated_at: string;
  content_type: string;
}

/** Dim-5: static formulation action skill (playbook). */
export interface FormulationSkillChecklistItem {
  id: string;
  title: string;
}

export interface FormulationSkill {
  id: string;
  title: string;
  summary: string;
  when_to_use: string;
  action: string;
  modal: string | null;
  icon: string;
  tools: string[];
  checklist: FormulationSkillChecklistItem[];
  presets: Record<string, unknown>;
}

export interface BatchUpdateRequest {
  campaign_id: number;
  rows: Array<{
    id: number;
    status: string;
    actual_params: Record<string, number>;
    measurements: Record<string, number | string>;
    note?: string | null;
    tags?: string[];
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
  /** Present when kg ingest raised; paired with kg_written=-1. */
  kg_error?: string | null;
  loop_task_id?: string | null;
  loop_message?: string;
  loop_status?: WorkbenchCampaignResponse["loop_status"];
  quality?: { dropped_values?: number; dropped?: string[]; [k: string]: unknown };
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

export interface KBSourceItem {
  id: string;
  title: string;
  filename: string;
  source_kind: string;
  origin_url?: string | null;
  project_id?: string | null;
  raw_text_chars: number;
  extraction_status: string;
  archived?: boolean;
}

export interface KBSourcesResponse {
  sources: KBSourceItem[];
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
  source?: string;
  requirement_fit?: number;
  /** Post-A′ #1: supply risk badges from material_suppliers. */
  supply_badges?: string[];
  stale_price?: boolean;
  missing_price?: boolean;
  long_lead_time?: boolean;
  evidence?: Array<{
    source_id?: string;
    chunk_id?: string | null;
    sentence?: string;
    confidence?: number | null;
  }>;
}

export interface ExternalSubstituteCandidate {
  name: string;
  iupac_name?: string | null;
  cas_no?: string | null;
  smiles?: string | null;
  cid?: number | null;
  formula?: string | null;
  molar_mass?: number | null;
  similarity: number;
  source: string;
  in_catalog: boolean;
  catalog_name?: string | null;
  role_hint?: string | null;
  note?: string | null;
}

export interface LiteratureSubstituteCandidate {
  name: string;
  source: "kg" | "kb_product" | "kb_chunk" | string;
  confidence?: number | null;
  entity_id?: string | null;
  cas_no?: string | null;
  smiles?: string | null;
  role_hint?: string | null;
  in_catalog: boolean;
  catalog_name?: string | null;
  evidence?: Array<{
    source_id?: string;
    chunk_id?: string | null;
    sentence?: string;
    confidence?: number | null;
  }>;
  note?: string | null;
}

export interface LlmSubstituteCandidate {
  name: string;
  kind?: string | null;
  rationale?: string | null;
  source: "chemist_rules" | "llm_expand" | "llm" | string;
  cas_no?: string | null;
  smiles?: string | null;
  role_hint?: string | null;
  in_catalog: boolean;
  catalog_name?: string | null;
  note?: string | null;
}

export interface SurechemblPatent {
  doc_id: string;
  title?: string | null;
  assignee?: string | null;
  pub_date?: string | null;
  url?: string | null;
}

export interface SurechemblSubstituteCandidate {
  name: string;
  chemical_id?: string | null;
  smiles?: string | null;
  inchi_key?: string | null;
  formula?: string | null;
  molar_mass?: number | null;
  similarity: number;
  global_frequency?: number | null;
  source: "surechembl" | string;
  in_catalog: boolean;
  catalog_name?: string | null;
  role_hint?: string | null;
  patents?: SurechemblPatent[];
  note?: string | null;
}

export interface SubstitutionIdentity {
  query: string;
  cas_no?: string;
  smiles?: string | null;
  cid?: number | null;
  source: string;
  resolved: boolean;
}

export interface SubstitutionExternalMeta {
  enabled: boolean;
  queried: boolean;
  count: number;
  skipped_reason?: string | null;
  provider: string;
}

export interface SubstitutionLiteratureMeta {
  enabled: boolean;
  queried: boolean;
  count: number;
  skipped_reason?: string | null;
  providers: string[];
}

export interface SubstitutionSurechemblMeta {
  enabled: boolean;
  queried: boolean;
  count: number;
  skipped_reason?: string | null;
  provider: string;
  search_hash?: string | null;
}

export interface SubstitutionLlmMeta {
  enabled: boolean;
  queried: boolean;
  count: number;
  skipped_reason?: string | null;
  mode?: "auto" | "forced" | "off" | string;
  providers?: string[];
}

export interface SubstitutionReport {
  original: string;
  original_in_catalog?: boolean;
  slot_index: number;
  role: string;
  substitute_group: string | null;
  base_metrics: Record<string, number>;
  candidates: SubstituteCandidate[];
  total_considered: number;
  identity?: SubstitutionIdentity;
  external?: ExternalSubstituteCandidate[];
  external_meta?: SubstitutionExternalMeta;
  literature?: LiteratureSubstituteCandidate[];
  literature_meta?: SubstitutionLiteratureMeta;
  surechembl?: SurechemblSubstituteCandidate[];
  surechembl_meta?: SubstitutionSurechemblMeta;
  llm?: LlmSubstituteCandidate[];
  llm_meta?: SubstitutionLlmMeta;
  layers_used?: string[];
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
  autodeposition_coating: "salt_spray_hours",
};

export function primaryObjectiveMetric(req: Requirement): string {
  if (req.objectives?.length) return req.objectives[0].metric;
  return OBJECTIVE_METRIC[req.domain];
}

/** Normalized API failure for store actions and UI banners. */
export interface ProjectDetailResponse {
  id: string;
  title: string;
  headline: string;
  domain: string;
  created_at: string;
  updated_at: string;
  workspace: import("../projectWorkspace").ProjectWorkspacePayload;
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

// --- types formerly below export const api ---
export type SearchSourceType = "patents" | "literature" | "internet" | "local" | "notebooklm" | "surechembl";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  citations?: Evidence[];
  /** Persistent-KB chunks that grounded this assistant answer. */
  kbChunksUsed?: number;
  /** SSE 流式问答中: 该条 assistant 消息仍在接收(逐 token 累积)。 */
  streaming?: boolean;
  /** SSE 阶段指示: retrieval | tools | answering | claims(仅 streaming 时有意义)。 */
  phase?: string;
  /** Live tool status while streaming (e.g. 结构识别中). */
  toolStatus?: string | null;
  /** Claim-check chips from SSE done (when chat_claim_check_enabled). */
  sourcedClaims?: SourcedClaim[] | null;
  /** Soft clarification prompt from SSE done (when chat_clarification_enabled). */
  clarification?: ClarificationOption | null;
}

/** /api/chat/stream 的 SSE 事件(后端 data: JSON 一行一个)。 */
export type ChatStreamEvent =
  | { type: "phase"; phase: "retrieval" | "tools" | "answering" | "claims" }
  | {
      type: "meta";
      kb_used: number;
      rewritten_query?: string | null;
      source_count?: number;
    }
  | { type: "tool_start"; name: string; args: Record<string, unknown>; label?: string }
  | { type: "tool_result"; name: string; ok: boolean; summary: string }
  | { type: "token"; delta: string }
  | {
      type: "done";
      answer: string;
      citations?: Evidence[];
      kb_chunks_used?: number;
      clarification?: ClarificationOption | null;
      rewritten_query?: string | null;
      sourced_claims?: SourcedClaim[] | null;
      structured?: StructuredAnswer | null;
      tools_used?: string[];
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
  /** Per-project NotebookLM notebook id (required when source_types includes notebooklm). */
  notebooklm_notebook_id?: string;
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
  /** Global auth ready (lib + enabled + session); notebook id is per-project. */
  auth_ready?: boolean;
  offline_fallback?: boolean;
}

export interface NotebookLMLoginResult {
  started: boolean;
  mode: "browser" | "manual";
  reason?: string | null;
  hint?: string | null;
  command?: string | null;
  manual_url?: string | null;
}

/** Public `/health` payload — coarse infra booleans only. */
export interface PlatformHealth {
  status: "ok" | "degraded";
  database: { ok: boolean; scheme: string };
  task_broker: { required: boolean; reachable: boolean };
  parsers: Record<string, boolean>;
  datalab: {
    required: boolean;
    reachable: boolean;
    hint?: string;
    campaign_backend?: string;
    experiment_backend?: string;
    /** eln | local | eln_required_down — Top-5″ soft-degrade badge */
    ledger_mode?: "eln" | "local" | "eln_required_down" | string;
  };
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
  /** Per-source counts in the kept list (audit: arXiv≈0, ChemRxiv visible). */
  source_counts?: Record<string, number>;
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
  /** Process-local quality-gate drop counters (retrieval + ingest). */
  quality_gate_drops?: KbGateDropStats;
  /** W4: active (non-archived) sources / chunks vs soft-archived. */
  sources_active?: number;
  sources_archived?: number;
  chunks_active?: number;
  chunks_archived?: number;
  /** W4: kb_search_scan_limit and chunks_active / scan_limit (capped at 1). */
  scan_limit?: number;
  scan_pressure?: number;
  scan_near_cap?: boolean;
  /** W4: recommended retention days (0 = disabled; purge never auto-runs). */
  archive_retention_days?: number;
  /** W4: whether materials.suppliers_json is still dual-written. */
  suppliers_json_dual_write?: boolean;
  stale_chunks?: number;
  products_pending_structure?: number;
  /** Active embedding model id (FORMUMIND_EMBEDDING_MODEL or default MiniLM). */
  embedding_model?: string;
  /** Explicit env override when set; null/undefined means default path. */
  embedding_model_configured?: string | null;
  /** Recommended upgrade catalog (MiniLM / bge / Qwen3-Embedding …). */
  embedding_catalog?: Array<{ id: string; label: string; langs?: string; note?: string }>;
  /** Reminder: model switch requires reindex. */
  reindex_hint?: string;
}

/** W4: POST /api/kb/retention/purge result. */
export interface RetentionPurgeResult {
  ok: boolean;
  dry_run: boolean;
  days: number;
  candidates: Array<{ source_id: string; title?: string | null; archived_at?: string | null }>;
  candidate_count: number;
  purged: string[];
  purged_count: number;
  errors: Array<{ source_id?: string; error?: string }>;
}

/** Nested drop counters from kb_retrieval_gate. */
export interface KbGateDropStats {
  retrieval?: { blocked_domain?: number; garbage_snippet?: number; wiki_track?: number };
  ingest?: { blocked_domain?: number; garbage_snippet?: number; wiki_track?: number };
}

/** Shared probe ↔ recommend knobs (GET /api/kb/retrieval-settings). */
export interface KbRetrievalSettings {
  kb_hybrid_alpha: number;
  kb_recommend_use_hybrid: boolean;
  kb_recommend_include_global: boolean;
  kb_recommend_top_k: number;
  kb_recommend_rerank_enabled: boolean;
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
  /** Batch E: stable | beta | experimental | disabled */
  maturity?: string;
}

/** Batch D: GET /api/kb/quality-ops */
export interface KBQualityOps {
  project_id?: string | null;
  kb_quality_score: number;
  kb_quality_components?: Record<string, number>;
  sources_active?: number;
  sources_archived?: number;
  chunks_active?: number;
  chunks_archived?: number;
  embedded_chunks?: number;
  scan_limit?: number;
  scan_pressure?: number;
  scan_near_cap?: boolean;
  vector_mode?: string;
  vector_hint?: string;
  embedding_model?: string;
  embedding_catalog?: Array<{ id: string; label: string; langs?: string; note?: string }>;
  reindex_hint?: string;
  topicality_would_reject_pct?: number | null;
  fulltext_fail_pct?: number | null;
  quality_gate_drops?: KbGateDropStats;
  relevance_shadow?: Record<string, unknown>;
  /** Post-A′ #3 / Option A: persistent hybrid_search latency (≠ session FAISS). */
  hybrid_search_latency?: {
    n?: number;
    p50_ms?: number | null;
    p95_ms?: number | null;
    ann_last?: boolean;
    ann_matrix_last?: boolean;
    ann_streak?: number;
    note?: string;
  };
  notes?: string[];
}

/** LLM Wiki read models (W1–W4). */
export interface WikiPageItem {
  id: string;
  path: string;
  kind: string;
  title: string;
  norm_key?: string;
  entity_id?: string | null;
  source_ids?: string[];
  flags?: string[];
  revision?: number;
  updated_at?: string | null;
}

export interface WikiPageDetail extends WikiPageItem {
  markdown: string;
}

export interface WikiPagesResponse {
  pages: WikiPageItem[];
  total: number;
}

export interface WikiSearchHit {
  id: string;
  path: string;
  kind: string;
  title: string;
  norm_key?: string;
  snippet?: string;
  flags?: string[];
  source_ids?: string[];
  rank?: number;
}

export interface WikiSearchResponse {
  hits: WikiSearchHit[];
  total: number;
  mode: string;
}

export interface WikiFlagAction {
  id: string;
  label: string;
  hint?: string;
  /** Optional path to open (S1 action target). */
  target?: string;
  /** S5: broken [[target]] for apply-broken chips. */
  broken?: string;
  /** S5: existing wiki path to rewrite/append toward. */
  replacement_path?: string;
  /** S5: rewrite | append_related */
  mode?: string;
}

export interface WikiFlagItem {
  id: string;
  path: string;
  kind: string;
  title: string;
  norm_key?: string;
  flags: string[];
  source_ids: string[];
  actions?: WikiFlagAction[];
}

export interface WikiFlagsResponse {
  pages: WikiFlagItem[];
}

/** Wiki page link graph (Hub canvas) — not materials KG. */
export interface WikiPageGraphNode {
  id: string;
  path: string;
  label: string;
  kind: string;
  flags?: string[];
  degree?: number;
  degree_in?: number;
  degree_out?: number;
  /** Weak-component community id (stable, size-ranked). */
  community?: number;
}

export interface WikiPageGraphEdge {
  source: string;
  target: string;
  weight?: number;
}

export interface WikiPageGraphMeta {
  node_count: number;
  edge_count: number;
  broken_links?: number;
  truncated?: boolean;
  scanned_pages?: number;
  elapsed_ms?: number;
  project_id?: string | null;
  include_orphan?: boolean;
  orphan_count?: number;
  isolate_count?: number;
  component_count?: number;
  largest_component?: number;
  community_count?: number;
  weighting?: string;
}

export interface WikiPageGraphInsightPage {
  path: string;
  label: string;
  kind: string;
  degree_in?: number;
  degree_out?: number;
  degree?: number;
}

export interface WikiPageGraphBrokenLink {
  source: string;
  source_label: string;
  target: string;
  reason?: string;
}

export interface WikiPageGraphInsights {
  orphans: WikiPageGraphInsightPage[];
  isolates: WikiPageGraphInsightPage[];
  broken: WikiPageGraphBrokenLink[];
  components?: { count: number; largest: number };
}

export interface WikiPageGraphResponse {
  ok: boolean;
  nodes: WikiPageGraphNode[];
  edges: WikiPageGraphEdge[];
  meta: WikiPageGraphMeta;
  insights?: WikiPageGraphInsights;
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
  /** False in production by default — server refuses POST /dependencies/install. */
  install_enabled?: boolean;
}

export interface DependencyInstallResult {
  ok: boolean;
  returncode?: number;
  summary: string;
  stdout?: string;
  stderr?: string;
}

/** Legacy poll fallback — prefer awaitTaskStream / subscribeTaskStream. */
export interface MaterialView {
  name: string;
  role: string;
  origin?: string;
  availability?: string;
  archived?: boolean;
  spec: Record<string, unknown>;
}

export interface MaterialListResponse {
  materials: MaterialView[];
  total?: number;
  store_enabled?: boolean;
}

export interface MaterialImportPreview {
  dry_run: boolean;
  batch_id: string;
  total: number;
  creates: number;
  updates: number;
  errors: number;
  rows: Array<{
    name: string;
    action: string;
    reason?: string;
    matched_by?: string;
    existing_name?: string;
  }>;
}

export interface MaterialCandidate {
  id: string;
  name: string;
  role?: string;
  cas_no?: string;
  smiles?: string;
  formula?: string;
  zh_name?: string;
  supplier?: string;
  source?: string;
  source_ref?: string;
  confidence?: string;
  status?: string;
  updated_at?: string | null;
}

export interface ChemicalHit {
  name: string;
  role?: string;
  smiles?: string | null;
  formula?: string | null;
  cas_no?: string | null;
  zh_name?: string | null;
  molar_mass?: number | null;
  /** scaffold-substitutes: 与查询目标的相似度/关系说明 */
  reason?: string;
  similarity?: number | null;
  availability?: string;
  spec?: Record<string, unknown>;
}

export interface SessionSummary {
  session_id: string;
  updated_at?: string | null;
  history_count?: number;
  title?: string | null;
  project_id?: string | null;
}

export interface SessionListResponse {
  sessions: SessionSummary[];
  total_count: number;
}

export interface SessionLoadResponse {
  history: unknown[];
  context: Record<string, unknown> | null;
  updated_at?: string | null;
  project_id?: string | null;
  title?: string | null;
}

export interface SessionInfoResponse {
  session_id: string;
  history_count: number;
  has_context: boolean;
  updated_at?: string | null;
  project_id?: string | null;
  title?: string | null;
}

export interface KbChunk {
  chunk_id?: string;
  source_id?: string;
  text?: string;
  content?: string;
  page?: number | null;
  paragraph?: number | null;
  offset?: number | null;
}

/** Chunk row from POST /api/kb/hybrid-search. */
export interface KbSearchChunk {
  id: string;
  source_id: string;
  ord: number;
  text: string;
  heading_path?: string;
  page?: number | null;
  paragraph?: number | null;
  offset_start?: number | null;
  offset_end?: number | null;
  meta?: Record<string, unknown> | null;
}

export type KbQueryTestMode = "keyword" | "hybrid" | "hybrid_rerank";

export interface KbQueryTestRequest {
  query: string;
  mode?: KbQueryTestMode;
  top_k?: number;
  alpha?: number;
  project_id?: string | null;
  include_global?: boolean;
  rerank?: boolean | null;
}

export interface KbQueryTestHit {
  rank: number;
  chunk_id?: string | null;
  source_id?: string | null;
  ord?: number | null;
  title: string;
  snippet: string;
  bm25_score?: number | null;
  cosine_score?: number | null;
  hybrid_score?: number | null;
  relevance?: number | null;
  rerank_score?: number | null;
  rank_before_rerank?: number | null;
  meta?: Record<string, unknown> | null;
}

export interface KbQueryTestResponse {
  query: string;
  mode: string;
  params: {
    top_k: number;
    alpha: number;
    project_id?: string | null;
    include_global?: boolean;
    rerank_applied?: boolean;
  };
  vector_mode: string;
  elapsed_ms: number;
  hits: KbQueryTestHit[];
  warning?: string | null;
  /** Drops during this probe run (hybrid path). */
  gate_drops?: KbGateDropStats;
  /** Process-lifetime counters at response time. */
  gate_drops_total?: KbGateDropStats;
}

export interface KbGoldenQuestion {
  question: string;
  expected_keywords: string[];
  category: string;
}

export interface KbGoldenEvalRequest {
  mode?: KbQueryTestMode;
  top_k?: number;
  alpha?: number;
  project_id?: string | null;
  include_global?: boolean;
  rerank?: boolean | null;
}

export interface KbGoldenEvalResultRow {
  question: string;
  category: string;
  passed: boolean;
  matched_keyword?: string | null;
  expected_keywords: string[];
  hit_titles: Array<string | null | undefined>;
  elapsed_ms?: number;
  warning?: string | null;
}

export interface KbGoldenEvalResponse {
  mode: string;
  top_k: number;
  alpha: number;
  project_id?: string | null;
  include_global?: boolean;
  total: number;
  passed: number;
  failed: number;
  /** W1′ / Top-5‴ #3 — mean reciprocal rank over the batch. */
  mrr?: number;
  /** Alias of passed/total for the keyword-hit gate. */
  recall_at_k?: number;
  results: KbGoldenEvalResultRow[];
}

/** GET /api/kg/calibration — ranking weights + relation hit counts. */
export interface KgCalibrationResponse {
  kg_enabled: boolean;
  kg_inhibits_penalty: number;
  kg_synergizes_bonus: number;
  kg_measured_bonus: number;
  kg_measured_metric_bonus?: number;
  kg_measured_metric_penalty?: number;
  kg_measured_metric_presence?: number;
  counts: { inhibits: number; substitutes: number; synergizes: number };
}

/** GET /api/experiments/search hit. */
export interface ExperimentSearchHit {
  row_id: number;
  campaign_id: number;
  campaign_name: string;
  item_id: string;
  status: string;
  planned_params: Record<string, unknown>;
  measurements: Record<string, unknown>;
}

/** GET /api/kg/path step. */
export interface KgPathStep {
  relation: KGRelationView;
  entity_id: string;
  entity_name?: string;
}

/** GET /api/kg/path response. */
export interface KgPathResponse {
  src_entity_id: string;
  dst_entity_id: string;
  found: boolean;
  hops: number;
  steps: KgPathStep[];
}

/** POST /api/kg/retrieve stats. */
export interface KgRetrieveStats {
  scan_total?: number;
  chunks_after_dedupe?: number;
  chunks_sent_to_llm?: number;
  mention_hits?: number;
  semantic_hits?: number;
  truncated?: boolean;
  trade_only?: boolean;
}

/** POST /api/kg/retrieve response. */
export interface KgRetrieveResponse {
  plan: {
    mode?: string;
    entity_ids?: string[];
    trade_only?: boolean;
    expanded_terms?: string[];
  };
  evidence: Evidence[];
  stats: KgRetrieveStats;
}

/** GET /api/kb/products row. */
export interface KbProductItem {
  trade_name: string;
  grade?: string;
  supplier?: string;
  generic_name?: string;
  cas?: string;
  smiles?: string | null;
  role?: string;
  mention_count?: number;
  sources?: number;
}

export interface KbProductsResponse {
  products: KbProductItem[];
  total: number;
}

/** Neo4j edge-link result. */
export interface Neo4jLinkResponse {
  ok: boolean;
  message: string;
}

export interface KbIntegrityResponse {
  healthy: boolean;
  total_orphans: number;
  external_backend: boolean;
  references: Array<Record<string, unknown>>;
}

export interface KgStats {
  enabled: boolean;
  entities: number;
  mentions: number;
  links: number;
  entities_by_kind?: Record<string, number>;
  links_by_type?: Record<string, number>;
  /** True when entities > 0 but kb_entity_links is still 0 (B10). */
  relation_layer_empty?: boolean;
  warnings?: string[];
}

/** Hub materials KG canvas (P2) — not Wiki page [[wikilink]] graph. */
export interface KgMaterialGraphNode {
  id: string;
  label: string;
  kind: string;
  degree?: number;
  degree_in?: number;
  degree_out?: number;
}

export interface KgMaterialGraphEdge {
  source: string;
  target: string;
  weight?: number;
  relation_type?: string;
  extraction_method?: string;
}

export interface KgMaterialGraphMeta {
  node_count: number;
  edge_count: number;
  truncated?: boolean;
  elapsed_ms?: number;
  relation_types?: string[];
  scanned_links?: number;
  backend?: string;
}

export interface KgMaterialGraphResponse {
  ok: boolean;
  nodes: KgMaterialGraphNode[];
  edges: KgMaterialGraphEdge[];
  meta: KgMaterialGraphMeta;
}

export interface KgRebuildReport {
  linked_sources: number;
  entities_upserted: number;
  mentions_upserted: number;
  links_created: number;
}

export interface KgLinkReport {
  source_id: string;
  entities_upserted: number;
  mentions_upserted: number;
  links_created: number;
  relations_upserted: number;
}

export interface Neo4jStats {
  enabled?: boolean;
  adapter_status?: string;
  reachable?: boolean;
  nodes?: number;
  edges?: number;
  compounds?: number;
  formulations?: number;
  detail?: Record<string, unknown>;
}

export interface Neo4jHit {
  uid?: string;
  name?: string;
  smiles?: string | null;
  similarity?: number | null;
  spec?: Record<string, unknown>;
}

export interface Neo4jCompound {
  uid: string;
  name?: string | null;
  smiles?: string | null;
  cas_number?: string | null;
  molecular_weight?: number | null;
  supplier?: string | null;
}

export interface Neo4jFormulation {
  uid: string;
  name?: string | null;
  target_property?: string | null;
  target_value?: number | null;
  status?: string | null;
}

export interface Supplier {
  name: string;
  url?: string | null;
  product_url?: string | null;
  /** A′ manual commercial fields (no scrape). */
  country?: string | null;
  currency?: string | null;
  price_cny_per_kg?: number | null;
  price_source?: string | null;
  price_observed_at?: string | null;
  moq?: string | null;
  pack_size?: string | null;
  lead_time_days?: number | null;
  /** Computed: price set but undated or older than retention window. */
  stale_price?: boolean;
}
