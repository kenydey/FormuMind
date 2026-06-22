import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  api,
  OBJECTIVE_METRIC,
  pollTask,
  type ChatMessage,
  type ComprehensiveReport,
  type DOEPlan,
  type Evidence,
  type ExperimentRecord,
  type Formulation,
  type LLMConfig,
  type LoopReport,
  type ModelInfo,
  type ObjectiveSpec,
  type ProductDomain,
  type Requirement,
  type ResearchResult,
  type SearchSourceType,
  type SourceStatus,
  type TaskStatus,
} from "./api";

export const DOMAIN_OBJECTIVES: Record<ProductDomain, ObjectiveSpec[]> = {
  anticorrosion_coating: [
    { metric: "salt_spray_hours", weight: 0.5, direction: "maximize" },
    { metric: "cost_cny_per_kg", weight: 0.25, direction: "minimize" },
    { metric: "sustainability_idx", weight: 0.25, direction: "maximize" },
  ],
  degreaser: [
    { metric: "cleaning_efficiency", weight: 0.5, direction: "maximize" },
    { metric: "cost_cny_per_kg", weight: 0.3, direction: "minimize" },
    { metric: "voc_gpl", weight: 0.2, direction: "minimize" },
  ],
  surface_treatment: [
    { metric: "salt_spray_hours", weight: 0.5, direction: "maximize" },
    { metric: "coating_weight_gsm", weight: 0.2, direction: "maximize" },
    { metric: "cost_cny_per_kg", weight: 0.3, direction: "minimize" },
  ],
};

export interface SessionSnapshot {
  id: string;
  timestamp: string;        // ISO-8601
  domain: ProductDomain;
  headline: string;
  topScore: number | null;
  requirement: Requirement;
  leaderboard: Formulation[];
  models: ModelInfo[];
  optimizationHistory: number[];
}

interface AppState {
  requirement: Requirement;
  research: ResearchResult | null;
  deepReport: ComprehensiveReport | null;
  task: TaskStatus | null;
  leaderboard: Formulation[];
  optimizationHistory: number[];
  busy: "idle" | "researching" | "optimizing" | "doe" | "training" | "looping";
  error: string | null;

  // v0.6 self-driving loop
  loopReport: LoopReport | null;
  rmseHistory: Record<string, number>[];   // one snapshot per loop turn
  intentBusy: boolean;

  // DOE feedback loop
  doePlan: DOEPlan | null;
  measured: Record<number, number>;
  models: ModelInfo[];
  modelHistory: ModelInfo[][];   // one entry per training event; for R² trend charts
  trainMessage: string;

  // Session history (persisted to localStorage)
  history: SessionSnapshot[];
  historyOpen: boolean;

  // v0.3 source + chat
  searchQuery: string;
  sourceTypes: SearchSourceType[];
  sources: Evidence[];
  sourceStatus: Record<string, SourceStatus>;
  chatHistory: ChatMessage[];
  searchBusy: boolean;
  chatBusy: boolean;
  // Modal 可见性
  openModal: string | null;   // "requirements" | "recommend" | "doe" | "optimize" | null
  // Settings
  llmConfig: LLMConfig;
  settingsOpen: boolean;

  setField: <K extends keyof Requirement>(key: K, value: Requirement[K]) => void;
  setDomain: (d: ProductDomain) => void;
  setObjectives: (objectives: ObjectiveSpec[]) => void;
  runResearch: () => Promise<void>;
  runDeepResearch: () => Promise<void>;
  runOptimize: () => Promise<void>;
  generateDoe: (design: string) => Promise<void>;
  setMeasured: (runId: number, value: number) => void;
  submitResults: () => Promise<void>;
  refreshModels: () => Promise<void>;
  exportDoe: (format: "csv" | "xlsx") => void;
  importCsv: (file: File) => Promise<void>;
  toggleHistory: () => void;
  restoreSnapshot: (snap: SessionSnapshot) => void;
  clearHistory: () => void;

  // v0.3 actions
  setSearchQuery: (q: string) => void;
  setSourceTypes: (types: SearchSourceType[]) => void;
  addSources: (evidence: Evidence[]) => void;
  removeSource: (id: string) => void;
  clearSources: () => void;
  searchSources: () => Promise<void>;
  loadSourceStatus: () => Promise<void>;
  uploadFile: (file: File) => Promise<void>;
  sendChat: (question: string) => Promise<void>;
  setOpenModal: (name: string | null) => void;
  setLlmConfig: (config: Partial<LLMConfig>) => void;
  toggleSettings: () => void;

  // v0.6 actions
  runLoop: () => Promise<void>;
  applyIntent: (text: string) => Promise<string[]>;
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
  objectives: [...DOMAIN_OBJECTIVES.anticorrosion_coating],
};

const MAX_HISTORY = 20;

function makeSnapshot(
  requirement: Requirement,
  leaderboard: Formulation[],
  models: ModelInfo[],
  optimizationHistory: number[],
): SessionSnapshot {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    timestamp: new Date().toISOString(),
    domain: requirement.domain,
    headline: `${requirement.domain} · ${requirement.substrate}`,
    topScore: leaderboard[0]?.score ?? null,
    requirement: { ...requirement },
    leaderboard,
    models,
    optimizationHistory,
  };
}

function pushToHistory(history: SessionSnapshot[], snap: SessionSnapshot): SessionSnapshot[] {
  return [snap, ...history].slice(0, MAX_HISTORY);
}

export const useStore = create<AppState>()(
  persist(
    (set, get) => ({
      requirement: defaultRequirement,
      research: null,
      deepReport: null,
      task: null,
      leaderboard: [],
      optimizationHistory: [],
      busy: "idle",
      error: null,
      doePlan: null,
      measured: {},
      models: [],
      modelHistory: [],
      trainMessage: "",
      history: [],
      historyOpen: false,

      // v0.3 initial state
      searchQuery: "",
      sourceTypes: ["patents", "literature"] as SearchSourceType[],
      sources: [],
      sourceStatus: {} as Record<string, SourceStatus>,
      chatHistory: [],
      searchBusy: false,
      chatBusy: false,
      openModal: null,
      llmConfig: { provider: "anthropic", model: "claude-sonnet-4-6", apiKey: "" },
      settingsOpen: false,

      // v0.6 initial state
      loopReport: null,
      rmseHistory: [],
      intentBusy: false,

      setField: (key, value) =>
        set((s) => ({ requirement: { ...s.requirement, [key]: value } })),

      setDomain: (d) =>
        set((s) => ({
          requirement: {
            ...s.requirement,
            domain: d,
            objectives: [...DOMAIN_OBJECTIVES[d]],
          },
        })),

      setObjectives: (objectives) =>
        set((s) => ({ requirement: { ...s.requirement, objectives } })),

      runResearch: async () => {
        set({ busy: "researching", error: null });
        try {
          const research = await api.research(get().requirement);
          const leaderboard = research.recommended;
          set({ research, leaderboard });
          // Save snapshot after research completes.
          const { requirement, models, optimizationHistory, history } = get();
          set({ history: pushToHistory(history, makeSnapshot(requirement, leaderboard, models, optimizationHistory)) });
        } catch (e) {
          set({ error: String(e) });
        } finally {
          set({ busy: "idle" });
        }
      },

      runDeepResearch: async () => {
        const { searchQuery, requirement } = get();
        set({ busy: "researching", error: null });
        try {
          const { task_id } = await api.deepResearch(searchQuery, requirement);
          const final = await pollTask(task_id, (t) => set({ task: t }));
          if (final.state === "failed") {
            set({ error: final.message || "deep research failed" });
            return;
          }
          const report = final.result as unknown as ComprehensiveReport | null;
          if (report) {
            set({ deepReport: report });
            // Feed citations into the sources panel and candidates into the board.
            if (report.citations?.length) get().addSources(report.citations);
            if (report.candidates?.length) set({ leaderboard: report.candidates });
            // Surface the report as an assistant message in the center Q&A area.
            const msg: ChatMessage = {
              role: "assistant",
              content: report.report_markdown,
              citations: report.citations,
            };
            set((s) => ({ chatHistory: [...s.chatHistory, msg] }));
          }
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
          if (final.result) {
            const leaderboard = final.result.top_formulations;
            const optHistory = final.result.history;
            set({ leaderboard, optimizationHistory: optHistory });
            const { requirement, models, history } = get();
            set({ history: pushToHistory(history, makeSnapshot(requirement, leaderboard, models, optHistory)) });
          }
        } catch (e) {
          set({ error: String(e) });
        } finally {
          set({ busy: "idle" });
        }
      },

      runLoop: async () => {
        set({ busy: "looping", error: null });
        try {
          const { task_id } = await api.loopIterate(get().requirement, 24, 4);
          const final = await pollTask(task_id, (t) => set({ task: t }));
          const report = final.result as unknown as LoopReport | null;
          if (report) {
            // Refresh leaderboard from the optimization, DOE from the next batch,
            // and append an RMSE snapshot for the closed-loop trend.
            const leaderboard = report.optimization.top_formulations;
            set((s) => ({
              loopReport: report,
              leaderboard,
              optimizationHistory: report.optimization.history,
              doePlan: report.next_doe,
              measured: {},
              models: report.model_info,
              rmseHistory: [...s.rmseHistory, report.rmse_by_metric],
            }));
            const { requirement, models, history } = get();
            set({ history: pushToHistory(history, makeSnapshot(requirement, leaderboard, models, report.optimization.history)) });
          }
        } catch (e) {
          set({ error: String(e) });
        } finally {
          set({ busy: "idle" });
        }
      },

      applyIntent: async (text) => {
        set({ intentBusy: true, error: null });
        try {
          const result = await api.parseIntent(text);
          // Merge parsed requirement into the form; keep objectives in sync with domain.
          set((s) => ({
            requirement: {
              ...s.requirement,
              ...result.requirement,
              objectives: DOMAIN_OBJECTIVES[result.requirement.domain] ?? s.requirement.objectives,
            },
          }));
          return result.extracted_fields;
        } catch (e) {
          set({ error: String(e) });
          return [];
        } finally {
          set({ intentBusy: false });
        }
      },

      generateDoe: async (design) => {
        set({ busy: "doe", error: null });
        try {
          let doePlan;
          if (design === "ai_active") {
            // Active learning endpoint: annotates most informative runs
            const req = get().requirement;
            const res = await fetch("/api/doe/active", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                ...req,
                existing_records: [],
                n_suggest: 4,
                doe_design: "lhs",
              }),
            });
            if (!res.ok) throw new Error(`/api/doe/active -> ${res.status}`);
            doePlan = await res.json();
          } else {
            doePlan = await api.doe(get().requirement, design);
          }
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
          const { modelHistory } = get();
          set({
            models: report.trained,
            modelHistory: [...modelHistory, report.trained],
            trainMessage: report.message,
          });
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
          const { modelHistory } = get();
          set({
            models: report.trained,
            modelHistory: [...modelHistory, report.trained],
            trainMessage: report.message,
          });
          await get().runResearch();
        } catch (e) {
          set({ error: `CSV 导入失败：${e instanceof Error ? e.message : String(e)}` });
        } finally {
          set({ busy: "idle" });
        }
      },

      toggleHistory: () => set((s) => ({ historyOpen: !s.historyOpen })),

      restoreSnapshot: (snap) => {
        set({
          requirement: snap.requirement,
          leaderboard: snap.leaderboard,
          models: snap.models,
          optimizationHistory: snap.optimizationHistory,
          research: null,         // research markdown isn't stored — show fresh context
          doePlan: null,
          measured: {},
          modelHistory: [],
          trainMessage: "",
          historyOpen: false,
          error: null,
        });
      },

      clearHistory: () => set({ history: [] }),

      // v0.3 actions
      setSearchQuery: (q) => set({ searchQuery: q }),

      setSourceTypes: (types) => set({ sourceTypes: types }),

      addSources: (evidence) =>
        set((s) => ({
          sources: [
            ...s.sources,
            ...evidence.filter(
              (e) => !s.sources.some((x) => (x.identifier || x.title) === (e.identifier || e.title))
            ),
          ],
        })),

      removeSource: (id) =>
        set((s) => ({ sources: s.sources.filter((e) => (e.identifier || e.title) !== id) })),

      clearSources: () => set({ sources: [], chatHistory: [] }),

      searchSources: async () => {
        const { searchQuery, sourceTypes, requirement } = get();
        set({ searchBusy: true, error: null });
        try {
          const res = await api.search({
            query: searchQuery,
            source_types: sourceTypes,
            requirement,
            limit_per_source: 5,
          });
          get().addSources(res.evidence);
          if (res.source_status) set({ sourceStatus: res.source_status });
        } catch (e) {
          set({ error: String(e) });
        } finally {
          set({ searchBusy: false });
        }
      },

      loadSourceStatus: async () => {
        try {
          const status = await api.getSourceStatus();
          set({ sourceStatus: status });
        } catch {
          // silently ignore — status badges just won't appear yet
        }
      },

      uploadFile: async (file) => {
        set({ searchBusy: true, error: null });
        try {
          const res = await api.ingest(file);
          get().addSources(res.evidence);
        } catch (e) {
          set({ error: `文件上传失败：${e instanceof Error ? e.message : String(e)}` });
        } finally {
          set({ searchBusy: false });
        }
      },

      sendChat: async (question) => {
        const { sources, requirement, chatHistory } = get();
        set({
          chatBusy: true,
          chatHistory: [...chatHistory, { role: "user", content: question }],
        });
        try {
          const res = await api.chat({
            question,
            sources,
            domain: requirement.domain,
          });
          set((s) => ({
            chatHistory: [
              ...s.chatHistory,
              { role: "assistant", content: res.answer, citations: res.citations },
            ],
          }));
        } catch (e) {
          set((s) => ({
            chatHistory: [
              ...s.chatHistory,
              { role: "assistant", content: `错误：${String(e)}` },
            ],
          }));
        } finally {
          set({ chatBusy: false });
        }
      },

      setOpenModal: (name) => set({ openModal: name }),

      setLlmConfig: (config) =>
        set((s) => ({ llmConfig: { ...s.llmConfig, ...config } })),

      toggleSettings: () => set((s) => ({ settingsOpen: !s.settingsOpen })),
    }),
    {
      name: "formumind-history",
      // Persist history list and llmConfig.
      partialize: (state) => ({ history: state.history, llmConfig: state.llmConfig }),
    }
  )
);
