// Typed backend client. Mirrors the FastAPI domain schemas.

export type ProductDomain = "anticorrosion_coating" | "degreaser" | "surface_treatment";

export interface Requirement {
  domain: ProductDomain;
  substrate: string;
  salt_spray_hours: number;
  film_weight_gsm: number;
  cure_temperature_c: number;
  cleaning_efficiency: number;
  voc_limit_gpl: number;
  ph_target: number | null;
  notes: string;
}

export interface Ingredient {
  name: string;
  role: string;
  weight_pct: number;
  formula?: string | null;
  smiles?: string | null;
  molar_mass?: number | null;
}

export interface ObjectiveSpec {
  metric: string;
  weight: number;
  direction: "maximize" | "minimize";
}

export interface Formulation {
  name: string;
  domain: ProductDomain;
  ingredients: Ingredient[];
  rationale: string;
  predicted: Record<string, number>;
  predicted_std: Record<string, number>;
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
}

export interface TaskStatus {
  task_id: string;
  kind: string;
  state: "pending" | "running" | "completed" | "failed";
  progress: number;
  message: string;
  result: OptimizationResult | null;
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
  factors: Record<string, number>;
  cure_temperature_c?: number | null;
  measured: Record<string, number>;
  source?: string;
  label?: string;
}

export interface ModelInfo {
  domain: ProductDomain;
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

// Objective metric collected per domain (mirrors backend OBJECTIVE map).
export const OBJECTIVE_METRIC: Record<ProductDomain, string> = {
  anticorrosion_coating: "salt_spray_hours",
  degreaser: "cleaning_efficiency",
  surface_treatment: "salt_spray_hours",
};

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

export const api = {
  research: (req: Requirement) => post<ResearchResult>("/api/research", req),
  doe: (req: Requirement, design: string) => post<DOEPlan>(`/api/doe?design=${design}`, req),
  startOptimize: (req: Requirement, iterations: number) =>
    post<{ task_id: string; poll_url: string }>("/api/optimize", { requirement: req, iterations }),
  task: async (id: string): Promise<TaskStatus> => {
    const res = await fetch(`/api/tasks/${id}`);
    if (!res.ok) throw new Error(`task ${id} -> ${res.status}`);
    return res.json();
  },
  submitExperiments: (records: ExperimentRecord[]) =>
    post<TrainingReport>("/api/experiments", { records, retrain: true }),
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
};

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
