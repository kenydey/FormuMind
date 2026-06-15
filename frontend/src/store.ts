import { create } from "zustand";
import {
  api,
  OBJECTIVE_METRIC,
  pollTask,
  type DOEPlan,
  type ExperimentRecord,
  type Formulation,
  type ModelInfo,
  type ProductDomain,
  type Requirement,
  type ResearchResult,
  type TaskStatus,
} from "./api";

interface AppState {
  requirement: Requirement;
  research: ResearchResult | null;
  task: TaskStatus | null;
  leaderboard: Formulation[];
  optimizationHistory: number[];  // best-so-far convergence curve
  busy: "idle" | "researching" | "optimizing" | "doe" | "training";
  error: string | null;

  // DOE feedback loop
  doePlan: DOEPlan | null;
  measured: Record<number, number>; // run_id -> measured objective value
  models: ModelInfo[];
  trainMessage: string;

  setField: <K extends keyof Requirement>(key: K, value: Requirement[K]) => void;
  setDomain: (d: ProductDomain) => void;
  runResearch: () => Promise<void>;
  runOptimize: () => Promise<void>;
  generateDoe: (design: string) => Promise<void>;
  setMeasured: (runId: number, value: number) => void;
  submitResults: () => Promise<void>;
  refreshModels: () => Promise<void>;
  exportDoe: (format: "csv" | "xlsx") => void;
  importCsv: (file: File) => Promise<void>;
}

const defaultRequirement: Requirement = {
  domain: "anticorrosion_coating",
  substrate: "carbon_steel",
  salt_spray_hours: 500,
  film_weight_gsm: 70,
  cure_temperature_c: 80,
  cleaning_efficiency: 90,
  voc_limit_gpl: 420,
  ph_target: null,
  notes: "",
};

export const useStore = create<AppState>((set, get) => ({
  requirement: defaultRequirement,
  research: null,
  task: null,
  leaderboard: [],
  optimizationHistory: [],
  busy: "idle",
  error: null,
  doePlan: null,
  measured: {},
  models: [],
  trainMessage: "",

  setField: (key, value) =>
    set((s) => ({ requirement: { ...s.requirement, [key]: value } })),

  setDomain: (d) => set((s) => ({ requirement: { ...s.requirement, domain: d } })),

  runResearch: async () => {
    set({ busy: "researching", error: null });
    try {
      const research = await api.research(get().requirement);
      set({ research, leaderboard: research.recommended });
    } catch (e) {
      set({ error: String(e) });
    } finally {
      set({ busy: "idle" });
    }
  },

  runOptimize: async () => {
    set({ busy: "optimizing", error: null });
    try {
      const { task_id } = await api.startOptimize(get().requirement, 24);
      const final = await pollTask(task_id, (t) => set({ task: t }));
      if (final.result) set({
        leaderboard: final.result.top_formulations,
        optimizationHistory: final.result.history,
      });
    } catch (e) {
      set({ error: String(e) });
    } finally {
      set({ busy: "idle" });
    }
  },

  generateDoe: async (design) => {
    set({ busy: "doe", error: null });
    try {
      const doePlan = await api.doe(get().requirement, design);
      set({ doePlan, measured: {} });
    } catch (e) {
      set({ error: String(e) });
    } finally {
      set({ busy: "idle" });
    }
  },

  setMeasured: (runId, value) =>
    set((s) => ({ measured: { ...s.measured, [runId]: value } })),

  submitResults: async () => {
    const { doePlan, measured, requirement } = get();
    if (!doePlan) return;
    const metric = OBJECTIVE_METRIC[requirement.domain];
    const records: ExperimentRecord[] = doePlan.runs
      .filter((r) => measured[r.run_id] !== undefined && !Number.isNaN(measured[r.run_id]))
      .map((r) => ({
        domain: requirement.domain,
        factors: r.natural,
        cure_temperature_c: r.natural["cure_temperature_c"] ?? null,
        measured: { [metric]: measured[r.run_id] },
        source: "doe",
      }));
    if (records.length === 0) {
      set({ error: "请先为至少一个实验填写实测值" });
      return;
    }
    set({ busy: "training", error: null });
    try {
      const report = await api.submitExperiments(records);
      set({ models: report.trained, trainMessage: report.message });
      // Refresh recommendations so the newly trained model takes effect.
      await get().runResearch();
    } catch (e) {
      set({ error: String(e) });
    } finally {
      set({ busy: "idle" });
    }
  },

  refreshModels: async () => {
    try {
      set({ models: await api.models() });
    } catch (e) {
      set({ error: String(e) });
    }
  },

  exportDoe: (format) => {
    const { doePlan } = get();
    if (!doePlan?.plan_id) {
      set({ error: "请先生成 DOE 计划再导出" });
      return;
    }
    window.open(api.doeExportUrl(doePlan.plan_id, format), "_blank");
  },

  importCsv: async (file) => {
    set({ busy: "training", error: null });
    try {
      const report = await api.importExperimentsCsv(file, get().requirement.domain);
      set({ models: report.trained, trainMessage: report.message });
      await get().runResearch();
    } catch (e) {
      set({ error: `CSV 导入失败：${e instanceof Error ? e.message : String(e)}` });
    } finally {
      set({ busy: "idle" });
    }
  },
}));
