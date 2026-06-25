import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  applyWorkspacePayload,
  buildWorkspacePayload,
  isLegacyMigrated,
  legacySnapshotsFromStorage,
  markLegacyMigrated,
  type ProjectSummary,
  type StoreWorkspaceSlice,
} from "./projectWorkspace";
import {
  api,
  awaitTaskStream,
  progressToTaskStatus,
  type ChatMessage,
  type ComprehensiveReport,
  type DOEPlan,
  type Evidence,
  type ExperimentRecord,
  type Formulation,
  type LeverSpec,
  type LLMConfig,
  type LoopReport,
  type ModelInfo,
  type ObjectiveSpec,
  type OptimizationResult,
  type ProcessOptResult,
  type ProductDomain,
  type Requirement,
  type ResearchResult,
  type SearchSourceType,
  type SourceStatus,
  type TaskStatus,
} from "./api";
import {
  objectiveMetrics,
  extractMeasuredValues,
} from "./utils/objectiveContract";
import {
  defaultConstraintsForDomain,
  type ConstraintKey,
} from "./constants/constraints";

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

export type { ProjectSummary } from "./projectWorkspace";

/** @deprecated use ProjectSummary — kept for migration typings */
export interface SessionSnapshot {
  id: string;
  timestamp: string;
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
  doeEngine: "auto" | "native" | "pydoe";
  alEngine: "auto" | "legacy" | "baybe";
  optimizeEngine: "auto" | "baybe" | "legacy";
  loopDoeEngine: "auto" | "legacy" | "baybe";
  campaignState: string | null;
  workbenchCampaignId: number | null;
  workbenchObjectivesSnapshot: ObjectiveSpec[] | null;
  workbenchStats: { completed: number; total: number; name: string; strategy: string } | null;
  lastAlEngine: string | null;
  models: ModelInfo[];
  modelHistory: ModelInfo[][];   // one entry per training event; for R² trend charts
  trainMessage: string;

  // NotebookLM-style project persistence (SQLite via API)
  projects: ProjectSummary[];
  activeProjectId: string | null;
  processOptResult: ProcessOptResult | null;
  projectSaveBusy: boolean;
  historyOpen: boolean;

  // v0.3 source + chat
  searchQuery: string;
  sourceTypes: SearchSourceType[];
  sources: Evidence[];
  selectedSources: string[];   // ids (identifier||title) of sources fed into Q&A
  sourceStatus: Record<string, SourceStatus>;
  chatHistory: ChatMessage[];
  searchBusy: boolean;
  deepResearchBusy: boolean;
  deepResearchStage: string;
  deepResearchMessage: string;
  formulationBusy: boolean;
  chatBusy: boolean;
  recommendSourceTypes: SearchSourceType[];
  openModal: string | null;   // requirements | recommend | doe | workbench | optimize | process | loop
  activeConstraints: ConstraintKey[];
  // Settings
  llmConfig: LLMConfig;
  settingsOpen: boolean;
  settingsTab: "llm" | "deps";

  setField: <K extends keyof Requirement>(key: K, value: Requirement[K]) => void;
  setDomain: (d: ProductDomain) => void;
  setObjectives: (objectives: ObjectiveSpec[]) => void;
  setLevers: (levers: LeverSpec[]) => void;
  loadExampleProject: (exampleId: string) => Promise<void>;
  setActiveConstraints: (keys: ConstraintKey[]) => void;
  setConstraintValue: (key: ConstraintKey, value: number) => void;
  clearConstraintValue: (key: ConstraintKey) => void;
  runResearch: () => Promise<void>;
  runDeepResearch: () => Promise<void>;
  refreshKnowledgeBase: () => Promise<void>;
  runOptimize: () => Promise<void>;
  generateDoe: (design: string) => Promise<void>;
  setDoeEngine: (engine: "auto" | "native" | "pydoe") => void;
  setAlEngine: (engine: "auto" | "legacy" | "baybe") => void;
  setOptimizeEngine: (engine: "auto" | "baybe" | "legacy") => void;
  setLoopDoeEngine: (engine: "auto" | "legacy" | "baybe") => void;
  setMeasured: (runId: number, value: number) => void;
  submitResults: () => Promise<void>;
  refreshWorkbenchStats: () => Promise<void>;
  ensureWorkbenchCampaign: () => Promise<number | null>;
  refreshModels: () => Promise<void>;
  exportDoe: (format: "csv" | "xlsx") => void;
  importCsv: (file: File) => Promise<void>;
  toggleHistory: () => void;
  initProjects: () => Promise<void>;
  scheduleAutosave: () => void;
  saveProject: () => Promise<void>;
  loadProject: (id: string) => Promise<void>;
  createProject: (title?: string) => Promise<void>;
  deleteProject: (id: string) => Promise<void>;
  setProcessOptResult: (result: ProcessOptResult | null) => void;

  // v0.3 actions
  setSearchQuery: (q: string) => void;
  setSourceTypes: (types: SearchSourceType[]) => void;
  setRecommendSourceTypes: (types: SearchSourceType[]) => void;
  addSources: (evidence: Evidence[]) => void;
  removeSource: (id: string) => void;
  clearSources: () => void;
  toggleSourceSelected: (id: string) => void;
  selectAllSources: () => void;
  deselectAllSources: () => void;
  searchSources: (queryOverride?: string) => Promise<void>;
  loadSourceStatus: () => Promise<void>;
  hydrateLlmSettings: () => Promise<void>;
  uploadFiles: (files: File[]) => Promise<void>;
  sendChat: (question: string) => Promise<void>;
  setOpenModal: (name: string | null) => void;
  setLlmConfig: (config: Partial<LLMConfig>) => void;
  toggleSettings: () => void;
  openSettings: (tab?: "llm" | "deps") => void;
  setSettingsTab: (tab: "llm" | "deps") => void;

  // v0.6 actions
  runLoop: () => Promise<void>;
  applyIntent: (text: string) => Promise<string[]>;
}

const defaultRequirement: Requirement = {
  project_id: "anticorrosion_coating",
  product_type: "防腐蚀环氧底漆",
  application: "carbon_steel",
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
  levers: [
    { name: "Zinc phosphate", low: 2, high: 14, unit: "wt%" },
    { name: "Bisphenol-A epoxy (DGEBA)", low: 28, high: 48, unit: "wt%" },
    { name: "Polyamide hardener", low: 8, high: 22, unit: "wt%" },
    { name: "cure_temperature_c", low: 50, high: 80, unit: "C" },
  ],
};

let autosaveTimer: ReturnType<typeof setTimeout> | null = null;
const AUTOSAVE_MS = 1500;

function workspaceSlice(state: AppState): StoreWorkspaceSlice {
  return {
    searchQuery: state.searchQuery,
    sourceTypes: state.sourceTypes,
    sources: state.sources,
    selectedSources: state.selectedSources,
    chatHistory: state.chatHistory,
    deepReport: state.deepReport,
    requirement: state.requirement,
    activeConstraints: state.activeConstraints,
    research: state.research,
    leaderboard: state.leaderboard,
    doePlan: state.doePlan,
    measured: state.measured,
    models: state.models,
    modelHistory: state.modelHistory,
    trainMessage: state.trainMessage,
    campaignState: state.campaignState,
    workbenchCampaignId: state.workbenchCampaignId,
    workbenchObjectivesSnapshot: state.workbenchObjectivesSnapshot,
    optimizationHistory: state.optimizationHistory,
    loopReport: state.loopReport,
    rmseHistory: state.rmseHistory,
    processOptResult: state.processOptResult,
    doeEngine: state.doeEngine,
    alEngine: state.alEngine,
    optimizeEngine: state.optimizeEngine,
    loopDoeEngine: state.loopDoeEngine,
    recommendSourceTypes: state.recommendSourceTypes,
    lastAlEngine: state.lastAlEngine,
  };
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
      doeEngine: "auto",
      alEngine: "auto",
      optimizeEngine: "auto",
      loopDoeEngine: "auto",
      campaignState: null,
      workbenchCampaignId: null,
      workbenchObjectivesSnapshot: null,
      workbenchStats: null,
      lastAlEngine: null,
      models: [],
      modelHistory: [],
      trainMessage: "",
      projects: [],
      activeProjectId: null,
      processOptResult: null,
      projectSaveBusy: false,
      historyOpen: false,

      // v0.3 initial state
      searchQuery: "",
      sourceTypes: ["patents", "literature", "internet"] as SearchSourceType[],
      sources: [],
      selectedSources: [],
      sourceStatus: {} as Record<string, SourceStatus>,
      chatHistory: [],
      searchBusy: false,
      deepResearchBusy: false,
      deepResearchStage: "",
      deepResearchMessage: "",
      formulationBusy: false,
      chatBusy: false,
      recommendSourceTypes: ["patents", "literature", "internet"] as SearchSourceType[],
      openModal: null,
      activeConstraints: defaultConstraintsForDomain("anticorrosion_coating"),
      llmConfig: { provider: "anthropic", model: "claude-sonnet-4-6", apiKey: "" },
      settingsOpen: false,
      settingsTab: "llm",

      // v0.6 initial state
      loopReport: null,
      rmseHistory: [],
      intentBusy: false,

      setField: (key, value) => {
        set((s) => ({ requirement: { ...s.requirement, [key]: value } }));
        get().scheduleAutosave();
      },

      setDomain: (d) => {
        set((s) => ({
          requirement: {
            ...s.requirement,
            domain: d,
            objectives: s.requirement.objectives.length
              ? s.requirement.objectives
              : [...DOMAIN_OBJECTIVES[d]],
          },
        }));
        get().scheduleAutosave();
      },

      setLevers: (levers) => {
        set((s) => ({ requirement: { ...s.requirement, levers } }));
        get().scheduleAutosave();
      },

      loadExampleProject: async (exampleId) => {
        set({ error: null });
        try {
          const req = await api.loadExampleProject(exampleId);
          set({
            requirement: req,
            activeConstraints: defaultConstraintsForDomain(req.domain),
            research: null,
            leaderboard: [],
            doePlan: null,
            measured: {},
          });
          get().scheduleAutosave();
        } catch (e) {
          set({ error: String(e) });
        }
      },

      setObjectives: (objectives) => {
        set((s) => ({ requirement: { ...s.requirement, objectives } }));
        get().scheduleAutosave();
      },

      setActiveConstraints: (keys) => {
        set({ activeConstraints: keys });
        get().scheduleAutosave();
      },

      setConstraintValue: (key, value) => {
        set((s) => ({ requirement: { ...s.requirement, [key]: value } }));
        get().scheduleAutosave();
      },

      clearConstraintValue: (key) => {
        set((s) => {
          const nullable: ConstraintKey[] = ["voc_limit_gpl", "cure_temperature_c", "ph_target"];
          const value = nullable.includes(key) ? null : 0;
          return { requirement: { ...s.requirement, [key]: value } };
        });
        get().scheduleAutosave();
      },

      runResearch: async () => {
        set({ formulationBusy: true, error: null });
        try {
          const { requirement, sources, selectedSources, searchQuery } = get();
          const selected = sources.filter((e) =>
            selectedSources.includes(e.identifier || e.title)
          );
          const payload = selected.length > 0 ? selected : sources;
          const research = await api.research(requirement, payload, searchQuery.trim());
          const leaderboard = research.recommended;
          set({ research, leaderboard });
          get().scheduleAutosave();
        } catch (e) {
          set({ error: String(e) });
        } finally {
          set({ formulationBusy: false });
        }
      },

      runDeepResearch: async () => {
        const { searchQuery, requirement, sources } = get();
        set({
          deepResearchBusy: true,
          deepResearchStage: "retrieve",
          deepResearchMessage: "正在检索",
          error: null,
        });
        try {
          const { task_id } = await api.submitDeepResearch(
            searchQuery,
            requirement,
            sources,
            searchQuery.trim()
          );
          const final = await awaitTaskStream(task_id, (ev) => {
            set({
              deepResearchStage: ev.stage ?? "",
              deepResearchMessage: ev.message ?? "",
              task: progressToTaskStatus(task_id, "deep_research", ev),
            });
          });
          const wrapped = final.data as { report?: ComprehensiveReport } | undefined;
          const report = wrapped?.report;
          if (!report) throw new Error("深度研究未返回结果");
          set({ deepReport: report });
          if (report.citations?.length) get().addSources(report.citations);
          if (report.candidates?.length) set({ leaderboard: report.candidates });
          const msg: ChatMessage = {
            role: "assistant",
            content: report.report_markdown,
            citations: report.citations,
          };
          set((s) => ({ chatHistory: [...s.chatHistory, msg] }));
          get().scheduleAutosave();
        } catch (e) {
          set({ error: String(e) });
        } finally {
          set({
            deepResearchBusy: false,
            deepResearchStage: "",
            deepResearchMessage: "",
          });
        }
      },

      refreshKnowledgeBase: async () => {
        const query = get().searchQuery.trim();
        if (!query) {
          set({ error: "请先输入研究主题" });
          return;
        }
        set({ searchBusy: true, error: null });
        try {
          const res = await api.refreshKnowledgeBase(query);
          set({
            deepResearchMessage: `已入库 ${res.fetched} 条（索引共 ${res.indexed_total}）`,
          });
        } catch (e) {
          set({ error: String(e) });
        } finally {
          set({ searchBusy: false });
        }
      },

      runOptimize: async () => {
        set({ busy: "optimizing", error: null });
        try {
          const { requirement, optimizeEngine, campaignState, workbenchCampaignId } = get();
          const { task_id } = await api.startOptimize(
            requirement,
            24,
            optimizeEngine,
            campaignState,
            workbenchCampaignId
          );
          const final = await awaitTaskStream(task_id, (ev) =>
            set({ task: progressToTaskStatus(task_id, "optimize", ev) })
          );
          const opt = final.data as unknown as OptimizationResult | null;
          if (opt?.top_formulations) {
            set({
              leaderboard: opt.top_formulations,
              optimizationHistory: opt.history,
            });
            get().scheduleAutosave();
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
          const { requirement, optimizeEngine, loopDoeEngine } = get();
          const { task_id } = await api.loopIterate(requirement, 24, 4, optimizeEngine, loopDoeEngine);
          const final = await awaitTaskStream(task_id, (ev) =>
            set({ task: progressToTaskStatus(task_id, "loop", ev) })
          );
          const report = final.data as unknown as LoopReport | null;
          if (report) {
            const leaderboard = report.optimization.top_formulations;
            set((s) => ({
              loopReport: report,
              leaderboard,
              optimizationHistory: report.optimization.history,
              doePlan: report.next_doe,
              measured: {},
              models: report.model_info,
              rmseHistory: [...s.rmseHistory, report.rmse_by_metric],
              lastAlEngine: report.engine,
            }));
            get().scheduleAutosave();
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
              objectives:
                result.requirement.objectives?.length
                  ? result.requirement.objectives
                  : s.requirement.objectives,
              levers: result.requirement.levers?.length
                ? result.requirement.levers
                : s.requirement.levers,
            },
          }));
          get().scheduleAutosave();
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
          const { requirement, doeEngine, alEngine, campaignState, workbenchCampaignId } = get();
          let plan: DOEPlan;
          let nextCampaignState = campaignState;
          let nextAlEngine: string | null = get().lastAlEngine;
          if (design === "ai_active") {
            const result = await api.activeDoe(requirement, {
              engine: alEngine,
              doe_engine: doeEngine,
              campaign_state: campaignState,
              workbench_campaign_id: workbenchCampaignId,
            });
            plan = result.plan;
            nextCampaignState = result.campaign_state ?? campaignState;
            nextAlEngine = result.engine;
          } else {
            plan = await api.doe(requirement, design, doeEngine);
          }
          const strategy = design === "ai_active" ? `BayBE-${alEngine}` : `DOE-${design}`;
          const { activeProjectId } = get();
          const wb = await api.createWorkbenchCampaign(
            plan,
            undefined,
            strategy,
            requirement,
            activeProjectId ?? undefined
          );
          set({
            doePlan: plan,
            measured: {},
            campaignState: nextCampaignState,
            lastAlEngine: nextAlEngine,
            workbenchCampaignId: wb.campaign_id,
            workbenchObjectivesSnapshot: wb.objectives_snapshot ?? null,
          });
          await get().refreshWorkbenchStats();
        } catch (e) {
          set({ error: String(e) });
        } finally {
          set({ busy: "idle" });
          get().scheduleAutosave();
        }
      },

      setDoeEngine: (engine) => {
        set({ doeEngine: engine });
        get().scheduleAutosave();
      },
      setAlEngine: (engine) => {
        set({ alEngine: engine });
        get().scheduleAutosave();
      },
      setOptimizeEngine: (engine) => {
        set({ optimizeEngine: engine });
        get().scheduleAutosave();
      },
      setLoopDoeEngine: (engine) => {
        set({ loopDoeEngine: engine });
        get().scheduleAutosave();
      },

      setMeasured: (runId, value) => {
        set((s) => ({ measured: { ...s.measured, [runId]: value } }));
        get().scheduleAutosave();
      },

      refreshWorkbenchStats: async () => {
        const id = get().workbenchCampaignId;
        if (id == null) {
          set({ workbenchStats: null });
          return;
        }
        try {
          const wb = await api.getWorkbenchCampaign(id);
          const completed = wb.rows.filter((r) => r.status === "Completed").length;
          set({
            workbenchStats: {
              completed,
              total: wb.rows.length,
              name: wb.name,
              strategy: wb.strategy,
            },
            workbenchObjectivesSnapshot: wb.objectives_snapshot ?? get().workbenchObjectivesSnapshot,
          });
        } catch {
          set({ workbenchStats: null });
        }
      },

      ensureWorkbenchCampaign: async () => {
        const { doePlan, workbenchCampaignId, requirement, activeProjectId } = get();
        if (workbenchCampaignId != null) {
          await get().refreshWorkbenchStats();
          return workbenchCampaignId;
        }
        if (!doePlan) return null;
        try {
          const strategy = doePlan.design === "ai_active" ? "BayBE-restore" : `DOE-${doePlan.design}`;
          const wb = await api.createWorkbenchCampaign(
            doePlan,
            undefined,
            strategy,
            requirement,
            activeProjectId ?? undefined
          );
          set({
            workbenchCampaignId: wb.campaign_id,
            workbenchObjectivesSnapshot: wb.objectives_snapshot ?? null,
          });
          get().scheduleAutosave();
          set({
            workbenchStats: {
              completed: 0,
              total: wb.rows.length,
              name: wb.name,
              strategy: wb.strategy,
            },
          });
          return wb.campaign_id;
        } catch (e) {
          set({ error: String(e) });
          return null;
        }
      },

      submitResults: async () => {
        const { doePlan, measured, requirement, workbenchCampaignId } = get();
        if (!doePlan) return;
        const metrics = objectiveMetrics(requirement);
        let records: ExperimentRecord[] = [];

        if (workbenchCampaignId != null) {
          try {
            const wb = await api.getWorkbenchCampaign(workbenchCampaignId);
            for (const r of wb.rows.filter((row) => row.status === "Completed")) {
              const measuredValues = extractMeasuredValues(r.measurements, metrics);
              if (!measuredValues) continue;
              records.push({
                domain: requirement.domain,
                project_id: requirement.project_id,
                factors: { ...(r.planned_params ?? {}), ...(r.actual_params ?? {}) },
                cure_temperature_c:
                  (r.actual_params?.cure_temperature_c ?? r.planned_params?.cure_temperature_c) ?? null,
                measured: measuredValues,
                source: "workbench",
              });
            }
          } catch (e) {
            set({ error: String(e) });
            return;
          }
        }

        if (records.length === 0) {
          const primary = metrics[0];
          records = doePlan.runs
            .filter((r) => measured[r.run_id] !== undefined && !Number.isNaN(measured[r.run_id]))
            .map((r) => ({
              domain: requirement.domain,
              project_id: requirement.project_id,
              factors: r.natural,
              cure_temperature_c: r.natural["cure_temperature_c"] ?? null,
              measured: { [primary]: measured[r.run_id] },
              source: "doe",
            }));
        }

        if (records.length === 0) {
          set({ error: "请先在实验台账中完成至少一行实测值，或为实验填写实测值" });
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

      scheduleAutosave: () => {
        if (autosaveTimer) clearTimeout(autosaveTimer);
        autosaveTimer = setTimeout(() => {
          void get().saveProject();
        }, AUTOSAVE_MS);
      },

      saveProject: async () => {
        const { activeProjectId } = get();
        if (!activeProjectId) return;
        set({ projectSaveBusy: true });
        try {
          const payload = buildWorkspacePayload(workspaceSlice(get()));
          const title = get().searchQuery.trim() || get().requirement.product_type || undefined;
          await api.updateProject(activeProjectId, payload, title);
          const projects = await api.listProjects();
          set({ projects });
        } catch (e) {
          set({ error: String(e) });
        } finally {
          set({ projectSaveBusy: false });
        }
      },

      loadProject: async (id) => {
        try {
          const { activeProjectId } = get();
          if (activeProjectId && activeProjectId !== id) {
            await get().saveProject();
          }
          const detail = await api.getProject(id);
          const patch = applyWorkspacePayload(detail.workspace, defaultRequirement);
          if (!patch.activeConstraints?.length && patch.requirement) {
            patch.activeConstraints = defaultConstraintsForDomain(patch.requirement.domain);
          }
          set({
            ...patch,
            activeProjectId: id,
            historyOpen: false,
            error: null,
            task: null,
            busy: "idle",
          });
          if (patch.workbenchCampaignId != null) {
            await get().refreshWorkbenchStats();
          } else {
            set({ workbenchStats: null });
          }
        } catch (e) {
          set({ error: String(e) });
        }
      },

      createProject: async (title = "") => {
        try {
          await get().saveProject();
          const detail = await api.createProject(title);
          const patch = applyWorkspacePayload(detail.workspace, defaultRequirement);
          set({
            ...patch,
            searchQuery: title || "",
            activeProjectId: detail.id,
            research: null,
            deepReport: null,
            leaderboard: [],
            chatHistory: [],
            sources: [],
            selectedSources: [],
            doePlan: null,
            measured: {},
            loopReport: null,
            rmseHistory: [],
            processOptResult: null,
            optimizationHistory: [],
            modelHistory: [],
            trainMessage: "",
            campaignState: null,
            workbenchCampaignId: null,
            workbenchObjectivesSnapshot: null,
            workbenchStats: null,
            error: null,
          });
          const projects = await api.listProjects();
          set({ projects });
        } catch (e) {
          set({ error: String(e) });
        }
      },

      deleteProject: async (id) => {
        try {
          const { activeProjectId } = get();
          if (activeProjectId && activeProjectId !== id) {
            await get().saveProject();
          }
          await api.deleteProject(id);
          const projects = await api.listProjects();
          if (activeProjectId === id) {
            set({ activeProjectId: null });
            if (projects.length > 0) {
              await get().loadProject(projects[0].id);
            } else {
              await get().createProject();
            }
          } else {
            set({ projects });
          }
        } catch (e) {
          set({ error: String(e) });
        }
      },

      initProjects: async () => {
        try {
          if (!isLegacyMigrated()) {
            const snaps = legacySnapshotsFromStorage();
            if (snaps.length) {
              await api.migrateLocalProjects(
                snaps.map((s) => ({
                  id: s.id,
                  timestamp: s.timestamp,
                  domain: s.domain,
                  headline: s.headline,
                  requirement: s.requirement,
                  leaderboard: s.leaderboard,
                  models: s.models,
                  optimization_history: s.optimizationHistory,
                }))
              );
              markLegacyMigrated();
            }
          }
          let projects = await api.listProjects();
          let activeId = get().activeProjectId;
          if (projects.length === 0) {
            const created = await api.createProject();
            projects = await api.listProjects();
            activeId = created.id;
          }
          if (!activeId || !projects.some((p) => p.id === activeId)) {
            activeId = projects[0]?.id ?? null;
          }
          set({ projects, activeProjectId: activeId });
          if (activeId) {
            const detail = await api.getProject(activeId);
            const patch = applyWorkspacePayload(detail.workspace, defaultRequirement);
            if (!patch.activeConstraints?.length && patch.requirement) {
              patch.activeConstraints = defaultConstraintsForDomain(patch.requirement.domain);
            }
            set({ ...patch, activeProjectId: activeId });
            if (patch.workbenchCampaignId != null) {
              await get().refreshWorkbenchStats();
            }
          }
        } catch (e) {
          set({ error: String(e) });
        }
      },

      setProcessOptResult: (result) => {
        set({ processOptResult: result });
        get().scheduleAutosave();
      },

      // v0.3 actions
      setSearchQuery: (q) => {
        set({ searchQuery: q });
        get().scheduleAutosave();
      },

      setSourceTypes: (types) => {
        set({ sourceTypes: types });
        get().scheduleAutosave();
      },

      setRecommendSourceTypes: (types) => {
        set({ recommendSourceTypes: types });
        get().scheduleAutosave();
      },

      addSources: (evidence) => {
        set((s) => {
          const fresh = evidence.filter(
            (e) => !s.sources.some((x) => (x.identifier || x.title) === (e.identifier || e.title))
          );
          const freshIds = fresh.map((e) => e.identifier || e.title);
          return {
            sources: [...s.sources, ...fresh],
            selectedSources: [...new Set([...s.selectedSources, ...freshIds])],
          };
        });
        get().scheduleAutosave();
      },

      removeSource: (id) => {
        set((s) => ({
          sources: s.sources.filter((e) => (e.identifier || e.title) !== id),
          selectedSources: s.selectedSources.filter((x) => x !== id),
        }));
        get().scheduleAutosave();
      },

      clearSources: () => {
        set({ sources: [], selectedSources: [], chatHistory: [] });
        get().scheduleAutosave();
      },

      toggleSourceSelected: (id) => {
        set((s) => ({
          selectedSources: s.selectedSources.includes(id)
            ? s.selectedSources.filter((x) => x !== id)
            : [...s.selectedSources, id],
        }));
        get().scheduleAutosave();
      },

      selectAllSources: () =>
        set((s) => ({ selectedSources: s.sources.map((e) => e.identifier || e.title) })),

      deselectAllSources: () => set({ selectedSources: [] }),

      searchSources: async (queryOverride?: string) => {
        const { searchQuery, requirement, sourceTypes } = get();
        const query = (queryOverride ?? searchQuery).trim();
        if (queryOverride !== undefined) set({ searchQuery: query });
        const types = sourceTypes.filter((t) => t !== "local");
        set({ searchBusy: true, error: null });
        try {
          const { task_id } = await api.searchStream({
            query,
            requirement,
            source_types: types.length ? types : undefined,
            total_limit: 300,
          });
          const final = await awaitTaskStream(task_id, (ev) => {
            const partial = ev.data?.evidence as Evidence[] | undefined;
            if (partial?.length) get().addSources(partial);
          });
          const r = final.data as
            | { evidence?: Evidence[]; source_status?: Record<string, SourceStatus> }
            | undefined;
          if (r?.evidence?.length) get().addSources(r.evidence);
          if (r?.source_status) set({ sourceStatus: r.source_status });
          get().scheduleAutosave();
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
          // silently ignore
        }
      },

      hydrateLlmSettings: async () => {
        try {
          const remote = await api.getSettings();
          const local = get().llmConfig;
          set({
            llmConfig: {
              provider: remote.provider || local.provider,
              model: remote.model || local.model,
              apiKey: local.apiKey,
              baseUrl: remote.base_url ?? local.baseUrl,
            },
          });
          if (local.apiKey && !remote.key_set) {
            await api.postSettings({
              provider: remote.provider || local.provider,
              model: remote.model || local.model,
              api_key: local.apiKey,
              baseUrl: remote.base_url ?? local.baseUrl,
            });
          }
        } catch {
          // offline — keep localStorage config
        }
      },

      uploadFiles: async (files) => {
        if (files.length === 0) return;
        set({ searchBusy: true, error: null });
        try {
          const res =
            files.length === 1
              ? await api.ingest(files[0])
              : await api.ingestBatch(files);
          get().addSources(res.evidence);
        } catch (e) {
          set({ error: `文件上传失败：${e instanceof Error ? e.message : String(e)}` });
        } finally {
          set({ searchBusy: false });
        }
      },

      sendChat: async (question) => {
        const { sources, selectedSources, requirement, chatHistory } = get();
        // Only the selected sources ground the answer (NotebookLM-style).
        const active = sources.filter((e) =>
          selectedSources.includes(e.identifier || e.title)
        );
        set({
          chatBusy: true,
          chatHistory: [...chatHistory, { role: "user", content: question }],
        });
        try {
          const res = await api.chat({
            question,
            sources: active,
            domain: requirement.domain,
          });
          set((s) => ({
            chatHistory: [
              ...s.chatHistory,
              { role: "assistant", content: res.answer, citations: res.citations },
            ],
          }));
          get().scheduleAutosave();
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

      openSettings: (tab = "llm") => set({ settingsOpen: true, settingsTab: tab }),

      setSettingsTab: (tab) => set({ settingsTab: tab }),
    }),
    {
      name: "formumind-history",
      partialize: (state) => ({
        activeProjectId: state.activeProjectId,
        llmConfig: state.llmConfig,
      }),
    }
  )
);
