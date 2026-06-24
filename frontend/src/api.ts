// Typed backend client. Mirrors the FastAPI domain schemas.

export type ProductDomain = "anticorrosion_coating" | "degreaser" | "surface_treatment";

export interface ObjectiveSpec {
  metric: string;
  weight: number;
  direction: "maximize" | "minimize";
  target_value?: number | null;
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
  constraints?: Record<string, number | null>;
}

export interface Ingredient {
  name: string;
  role: string;
  weight_pct: number;
  formula?: string | null;
  smiles?: string | null;
  molar_mass?: number | null;
}

export interface Formulation {
  name: string;
  domain: ProductDomain;
  ingredients: Ingredient[];
  rationale: string;
  predicted: Record<string, number>;
  predicted_std: Record<string, number>;
  prediction_tiers?: Record<string, string>;
  score: number | null;
  warnings: string[];
}

export interface Evidence {
  source: string;
  identifier: string;
  title: string;
  snippet: string;
  relevance: number;
}

export interface ResearchResult {
  requirement_headline: string;
  evidence: Evidence[];
  mechanism: string;
  recommended: Formulation[];
  chat_markdown: string;
}

export interface OptimizationResult {
  iterations: number;
  objective: string;
  objectives: ObjectiveSpec[];
  history: number[];
  top_formulations: Formulation[];
  engine?: string;
}

export interface ActiveDoeResult {
  plan: DOEPlan;
  campaign_state: string | null;
  engine: string;
}

export interface BaybeRecommendResult {
  plan: DOEPlan;
  campaign_state: string;
  engine: string;
}

export interface TaskStatus {
  task_id: string;
  kind: string;
  state: "pending" | "running" | "completed" | "failed";
  progress: number;
  message: string;
  result: Record<string, unknown> | null;
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

export interface WorkbenchRow {
  id: number;
  campaign_id: number;
  status: string;
  planned_params: Record<string, number>;
  actual_params: Record<string, number>;
  measurements: Record<string, number | string>;
}

export interface WorkbenchCampaignResponse {
  campaign_id: number;
  name: string;
  strategy: string;
  status: string;
  rows: WorkbenchRow[];
}

export interface BatchUpdateRequest {
  campaign_id: number;
  rows: Array<{
    id: number;
    status: string;
    actual_params: Record<string, number>;
    measurements: Record<string, number | string>;
  }>;
}

export interface WorkbenchSyncResponse {
  updated: number;
  rows: WorkbenchRow[];
}

// Objective metric collected per domain (mirrors backend OBJECTIVE map).
export const OBJECTIVE_METRIC: Record<ProductDomain, string> = {
  anticorrosion_coating: "salt_spray_hours",
  degreaser: "cleaning_efficiency",
  surface_treatment: "salt_spray_hours",
};

export function primaryObjectiveMetric(req: Requirement): string {
  if (req.objectives?.length) return req.objectives[0].metric;
  return OBJECTIVE_METRIC[req.domain];
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: "DELETE" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
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

export const api = {
  research: (
    req: Requirement,
    sources: Evidence[] = [],
    sourceTypes: SearchSourceType[] = ["patents"],
    query = ""
  ) =>
    post<ResearchResult>("/api/research", { ...req, sources, source_types: sourceTypes, query }),
  doe: (req: Requirement, design: string, engine = "auto") =>
    post<DOEPlan>(`/api/doe?design=${encodeURIComponent(design)}&engine=${encodeURIComponent(engine)}`, req),
  activeDoe: (
    req: Requirement,
    opts: {
      n_suggest?: number;
      doe_design?: string;
      engine?: string;
      doe_engine?: string;
      campaign_state?: string | null;
      existing_records?: ExperimentRecord[];
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
    }),
  startOptimize: (
    req: Requirement,
    iterations: number,
    engine = "auto",
    campaignState?: string | null
  ) =>
    post<{ task_id: string; poll_url: string }>("/api/optimize", {
      requirement: req,
      iterations,
      engine,
      campaign_state: campaignState ?? null,
    }),
  task: async (id: string): Promise<TaskStatus> => {
    const res = await fetch(`/api/tasks/${id}`);
    if (!res.ok) throw new Error(`task ${id} -> ${res.status}`);
    return res.json();
  },
  submitExperiments: (records: ExperimentRecord[]) =>
    post<TrainingReport>("/api/experiments", { records, retrain: true }),
  createWorkbenchCampaign: (plan: DOEPlan, name?: string, strategy?: string) =>
    post<WorkbenchCampaignResponse>("/api/experiments/workbench/campaigns", {
      plan,
      name,
      strategy,
    }),
  getWorkbenchCampaign: (campaignId: number) =>
    get<WorkbenchCampaignResponse>(`/api/experiments/workbench/${campaignId}`),
  syncWorkbench: (body: BatchUpdateRequest) =>
    put<WorkbenchSyncResponse>("/api/experiments/workbench/sync", body),
  models: () => get<ModelInfo[]>("/api/models"),
  doeExportUrl: (planId: string, format: "csv" | "xlsx" = "csv") =>
    `/api/doe/${planId}/export?format=${format}`,
  importExperimentsCsv: async (file: File, domain?: ProductDomain): Promise<TrainingReport> => {
    const fd = new FormData();
    fd.append("file", file);
    const q = domain ? `?domain=${domain}` : "";
    const res = await fetch(`/api/experiments/import-csv${q}`, { method: "POST", body: fd });
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

  searchStream: (req: SearchRequest) =>
    post<{ task_id: string; poll_url: string }>("/api/search/stream", req),

  notebooklmStatus: () =>
    get<NotebookLMStatus>("/api/notebooklm/auth-status"),

  notebooklmConfig: (cfg: { enabled?: boolean; notebook_id?: string }) =>
    post<NotebookLMStatus>("/api/notebooklm/config", cfg),

  notebooklmLogin: () =>
    post<NotebookLMLoginResult>("/api/notebooklm/login", {}),

  ingest: async (file: File): Promise<IngestResponse> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/ingest", { method: "POST", body: fd });
    if (!res.ok) throw new Error(`/api/ingest -> ${res.status}`);
    return res.json();
  },

  ingestBatch: async (files: File[]): Promise<IngestResponse & { files_processed?: number }> => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    const res = await fetch("/api/ingest/batch", { method: "POST", body: fd });
    if (!res.ok) throw new Error(`/api/ingest/batch -> ${res.status}`);
    return res.json();
  },

  ingestUrl: (url: string) =>
    post<IngestResponse>("/api/ingest/url", { url }),

  ingestText: (text: string, title?: string) =>
    post<IngestResponse>("/api/ingest/text", { text, title: title ?? "Pasted text" }),

  listProjects: () => get<import("./projectWorkspace").ProjectSummary[]>("/api/projects"),

  createProject: (title = "", requirement?: Requirement) =>
    post<ProjectDetailResponse>("/api/projects", { title, requirement }),

  getProject: (id: string) => get<ProjectDetailResponse>(`/api/projects/${encodeURIComponent(id)}`),

  updateProject: (id: string, workspace: import("./projectWorkspace").ProjectWorkspacePayload, title?: string) =>
    put<ProjectDetailResponse>(`/api/projects/${encodeURIComponent(id)}`, { workspace, title }),

  deleteProject: (id: string) =>
    del<{ ok: boolean }>(`/api/projects/${encodeURIComponent(id)}`),

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

  getSettings: () => get<LLMSettingsResponse>("/api/settings"),

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

  testConnection: () =>
    post<{ ok: boolean; provider: string; model: string; message: string }>(
      "/api/settings/test", {}
    ),

  analyzeIP: (req: IPAnalysisRequest) =>
    post<IPReport>("/api/ip/analyze", req),

  optimizeProcess: (req: ProcessOptRequest) =>
    post<ProcessOptResult>("/api/process-optimize", req),

  loopIterate: (
    req: Requirement,
    optimize_iterations = 24,
    n_suggest = 4,
    optimize_engine = "auto",
    doe_engine = "auto"
  ) =>
    post<{ task_id: string; poll_url: string }>("/api/loop/iterate", {
      ...req,
      optimize_iterations,
      n_suggest,
      optimize_engine,
      doe_engine,
    }),

  parseIntent: (text: string) =>
    post<IntentResult>("/api/intent/parse", { text }),

  loadExampleProject: (exampleId: string) =>
    get<Requirement>(`/api/examples/${encodeURIComponent(exampleId)}`),

  getSourceStatus: () =>
    get<Record<string, SourceStatus>>("/api/search/status"),

  deepResearch: (topic: string, req: Requirement, source_types: SearchSourceType[]) =>
    post<{ task_id: string; poll_url: string }>("/api/research/deep", {
      ...req,
      topic,
      source_types,
    }),

  listDependencies: () =>
    get<DependencyListResponse>("/api/dependencies"),

  installDependencies: (names: string[], upgrade = false) =>
    post<{ task_id: string; poll_url: string }>("/api/dependencies/install", {
      names,
      upgrade,
    }),
};

// ── v0.3 新增类型 ────────────────────────────────────────────────────────────

export type SearchSourceType = "patents" | "literature" | "internet" | "local" | "notebooklm";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  citations?: Evidence[];
}

export interface LLMModelOption {
  id: string;
  label: string;
  recommended?: boolean;
}

export interface LLMProviderInfo {
  id: string;
  label: string;
  base_url?: string;
  models: LLMModelOption[];
}

export interface LLMConfig {
  provider: string;
  model: string;
  apiKey: string;
  baseUrl?: string;
}

export interface SearchRequest {
  query?: string;
  source_types: SearchSourceType[];
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
}

export interface IngestResponse {
  filename: string;
  evidence: Evidence[];
  total: number;
}

export interface ChatRequest {
  question: string;
  sources: Evidence[];
  domain?: string;
}

export interface ChatResponse {
  answer: string;
  citations: Evidence[];
}

export interface LLMSettingsResponse {
  provider: string;
  model: string;
  key_set: boolean;
  base_url?: string;
  providers: LLMProviderInfo[];
}

// ── v0.5 新增类型 ────────────────────────────────────────────────────────────

export interface PatentRisk {
  patent_id: string;
  title: string;
  risk: "high" | "medium" | "low" | "unknown";
  claim_overlap: string;
  recommendation: string;
}

export interface IPReport {
  formulation_name: string;
  novelty_score: number;
  risks: PatentRisk[];
  whitespace_hints: string[];
  raw_patents_searched: number;
  engine: string;
}

export interface IPAnalysisRequest {
  formulation: Formulation;
  limit_patents?: number;
}

export interface ProcessOptRequest {
  domain: ProductDomain;
  iterations?: number;
}

export interface ProcessOptResult {
  domain: string;
  iterations: number;
  engine: string;
  history: number[];
  best_params: Record<string, number>;
  predicted_outcome: Record<string, number>;
}

// ── v0.6 新增类型 ────────────────────────────────────────────────────────────

export interface LoopReport {
  domain: string;
  total_records: number;
  model_info: ModelInfo[];
  rmse_by_metric: Record<string, number>;
  optimization: OptimizationResult;
  next_doe: DOEPlan;
  engine: string;
}

export interface IntentResult {
  requirement: Requirement;
  confidence: number;
  extracted_fields: string[];
  engine: string;
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

// Poll a task until it terminates, invoking onUpdate on each tick.
export async function pollTask(
  id: string,
  onUpdate: (s: TaskStatus) => void,
  intervalMs = 400
): Promise<TaskStatus> {
  for (;;) {
    const s = await api.task(id);
    onUpdate(s);
    if (s.state === "completed" || s.state === "failed") return s;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
