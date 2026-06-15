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

export interface Formulation {
  name: string;
  domain: ProductDomain;
  ingredients: Ingredient[];
  rationale: string;
  predicted: Record<string, number>;
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

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export const api = {
  research: (req: Requirement) => post<ResearchResult>("/api/research", req),
  doe: (req: Requirement, design: string) => post<unknown>(`/api/doe?design=${design}`, req),
  startOptimize: (req: Requirement, iterations: number) =>
    post<{ task_id: string; poll_url: string }>("/api/optimize", { requirement: req, iterations }),
  task: async (id: string): Promise<TaskStatus> => {
    const res = await fetch(`/api/tasks/${id}`);
    if (!res.ok) throw new Error(`task ${id} -> ${res.status}`);
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
