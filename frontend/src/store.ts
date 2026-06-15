import { create } from "zustand";
import {
  api,
  pollTask,
  type Formulation,
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
  busy: "idle" | "researching" | "optimizing";
  error: string | null;

  setField: <K extends keyof Requirement>(key: K, value: Requirement[K]) => void;
  setDomain: (d: ProductDomain) => void;
  runResearch: () => Promise<void>;
  runOptimize: () => Promise<void>;
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
  busy: "idle",
  error: null,

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
      if (final.result) set({ leaderboard: final.result.top_formulations });
    } catch (e) {
      set({ error: String(e) });
    } finally {
      set({ busy: "idle" });
    }
  },
}));
