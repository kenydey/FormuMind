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
  chemtools: { enabled: boolean; chemcrow_installed: boolean };
}

export interface ChemToolsCapability {
  available: boolean;
  hint?: string | null;
}

export interface ChemToolsStatus {
  enabled: boolean;
  chemcrow_installed: boolean;
  rdkit_installed: boolean;
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
  source?: string;
}

export interface Evidence {
  source: string;
  identifier: string;
  title: string;
  snippet: string;
  relevance: number;
  /** True when this row is from the offline seed corpus, not a live API hit. */
  is_seed_corpus?: boolean;
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

export type TaskProgressStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface TaskProgressEvent {
  status: TaskProgressStatus;
  stage?: string;
  message: string;
  progress?: number;
  data?: Record<string, unknown>;
}

export interface AsyncTaskAccepted {
  task_id: string;
  stream_url: string;
  status_url: string;
}

export interface TaskStatus {
  task_id: string;
  kind: string;
  state: "pending" | "running" | "completed" | "failed";
  progress: number;
  message: string;
  result: Record<string, unknown> | null;
  stream_url?: string;
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
  project_id?: string | null;
  primary_metric?: string | null;
  objectives_snapshot?: ObjectiveSpec[];
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
    }),
  baybeRecommend: (
    req: Requirement,
    opts: {
      batch_size?: number;
      campaign_state?: string | null;
      workbench_campaign_id?: number | null;
      existing_records?: ExperimentRecord[];
    } = {}
  ) =>
    post<BaybeRecommendResult>("/api/baybe/recommend", {
      ...req,
      existing_records: opts.existing_records ?? [],
      batch_size: opts.batch_size ?? 4,
      campaign_state: opts.campaign_state ?? null,
      workbench_campaign_id: opts.workbench_campaign_id ?? null,
    }),
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
  syncWorkbench: (body: BatchUpdateRequest) =>
    put<WorkbenchSyncResponse>("/api/experiments/workbench/sync", body),
  models: () => get<ModelInfo[]>("/api/models"),
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

  kbStats: () => get<KBStats>("/api/kb/stats"),

  kbReindex: () => post<KBReindexResult>("/api/kb/reindex", {}),

  getSettings: () => get<LLMSettingsResponse>("/api/settings"),

  getAuthStatus: () =>
    get<{ auth_required: boolean; hint: string }>("/api/auth/status"),

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

  getSecrets: () => get<SecretsListResponse>("/api/settings/secrets"),

  postSecrets: (updates: Record<string, string>) =>
    post<SecretsListResponse>("/api/settings/secrets", { updates }),

  testSecret: (id: string) =>
    post<{ ok: boolean; message: string }>("/api/settings/secrets/test", { id }),

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
    postAccepted("/api/loop/iterate", {
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

  refreshKnowledgeBase: (query: string) =>
    post<{ query: string; fetched: number; indexed_total: number; source_counts: Record<string, number> }>(
      `/api/research/kb/refresh?query=${encodeURIComponent(query)}`,
      {}
    ),

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
export function awaitTaskStream(
  taskId: string,
  onEvent?: (ev: TaskProgressEvent) => void,
  timeoutMs = 120_000,
  signal?: AbortSignal
): Promise<TaskProgressEvent> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let es: EventSource;

    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      fn();
    };

    const onAbort = () => {
      es?.close();
      finish(() => reject(new Error("任务已取消")));
    };

    const resolveFromStatus = (s: TaskStatus) => {
      const ev: TaskProgressEvent = {
        status: s.state === "completed" ? "COMPLETED" : "FAILED",
        message: s.message,
        progress: s.progress,
        data: s.result ?? undefined,
      };
      onEvent?.(ev);
      if (s.state === "completed") {
        finish(() => resolve(ev));
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

    es = subscribeTaskStream(
      taskId,
      (ev) => {
        onEvent?.(ev);
        if (ev.status === "COMPLETED" || ev.status === "FAILED") {
          es.close();
          if (ev.status === "FAILED") {
            finish(() => reject(new Error(ev.message || "任务失败")));
          } else {
            finish(() => resolve(ev));
          }
        }
      },
      () => {
        es.close();
        pollTask(taskId, (s) => {
          if (s.state === "running" || s.state === "pending") {
            onEvent?.({
              status: s.state === "running" ? "RUNNING" : "PENDING",
              message: s.message,
              progress: s.progress,
            });
          }
        })
          .then(resolveFromStatus)
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
} {
  if (!data) return { evidence: [], progress: {}, usedSeedFallback: false };
  const evidence = Array.isArray(data.evidence) ? (data.evidence as Evidence[]) : [];
  const usedSeedFallback =
    data.used_seed_fallback === true || evidence.some((e) => e.is_seed_corpus);
  return {
    evidence,
    usedSeedFallback,
    progress: {
      total: typeof data.total === "number" ? data.total : evidence.length,
      source: typeof data.source === "string" ? data.source : null,
      newCount: typeof data.new_count === "number" ? data.new_count : 0,
      sourcesDone: Array.isArray(data.sources_done) ? (data.sources_done as string[]) : [],
      sourcesPending: Array.isArray(data.sources_pending) ? (data.sources_pending as string[]) : [],
    },
  };
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
  /** Retrieval backend that served the citations (tfidf | embedding | colbert). */
  rag_backend?: string;
  /** Persistent-KB chunks merged into the grounding set for this answer. */
  kb_chunks_used?: number;
}

/** Persistent knowledge base counters (GET /api/kb/stats). */
export interface KBStats {
  enabled: boolean;
  sources: number;
  sources_by_kind: Record<string, number>;
  chunks: number;
  embedded_chunks: number;
  embedding_available: boolean;
}

export interface KBReindexResult {
  reindexed_sources: number;
  reindexed_chunks: number;
  total_chunks: number;
  embedded_chunks: number;
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
  maxAttempts = 300
): Promise<TaskStatus> {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const s = await api.task(id);
    onUpdate(s);
    if (s.state === "completed" || s.state === "failed") return s;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`任务轮询超时（${maxAttempts} 次）`);
}
