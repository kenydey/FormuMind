import { create } from "zustand";
import { persist } from "zustand/middleware";
import { immer } from "zustand/middleware/immer";
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
  type SearchStreamProgress,
  parseSearchStreamData,
  type SourceStatus,
  type TaskStatus,
} from "./api";
import {
  extractMeasuredValues,
  normalizeObjective,
  normalizeObjectives,
  objectiveMetrics,
} from "./utils/objectiveContract";
import {
  defaultConstraintsForDomain,
  constraintLabelForKey,
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
  requirementLocked: boolean;
  historyOpen: boolean;

  // v0.3 source + chat
  searchQuery: string;
  sourceTypes: SearchSourceType[];
  sources: Evidence[];
  selectedSources: string[];   // ids (identifier||title) of sources fed into Q&A
  sourceStatus: Record<string, SourceStatus>;
  chatHistory: ChatMessage[];
  searchBusy: boolean;
  searchProgress: SearchStreamProgress | null;
  deepResearchBusy: boolean;
  deepResearchStage: string;
  deepResearchMessage: string;
  formulationBusy: boolean;
  recommendStage: string;
  recommendMessage: string;
  chatBusy: boolean;
  recommendSourceTypes: SearchSourceType[];
  openModal: string | null;   // requirements | recommend | doe | workbench | optimize | process | loop
  activeConstraints: ConstraintKey[];
  // Settings
  llmConfig: LLMConfig;
  settingsOpen: boolean;
  settingsTab: "llm" | "deps" | "api";

  setField: <K extends keyof Requirement>(key: K, value: Requirement[K]) => void;
  setDomain: (d: ProductDomain) => void;
  setObjectives: (objectives: ObjectiveSpec[]) => void;
  updateObjective: (idx: number, patch: Partial<ObjectiveSpec>) => void;
  removeObjective: (idx: number) => void;
  addObjective: (objective: ObjectiveSpec) => void;
  resetObjectivesForDomain: (domain: ProductDomain) => void;
  setLevers: (levers: LeverSpec[]) => void;
  loadExampleProject: (exampleId: string) => Promise<void>;
  setActiveConstraints: (keys: ConstraintKey[]) => void;
  setConstraintValue: (key: ConstraintKey, value: number) => void;
  clearConstraintValue: (key: ConstraintKey) => void;
  setCustomConstraint: (name: string, value: number) => void;
  removeCustomConstraint: (name: string) => void;
  saveRequirementAndRefresh: () => Promise<void>;
  unlockRequirement: () => void;
  addManualFormulation: () => Promise<void>;
  modifyFormulationsWithAi: (prompt: string) => Promise<void>;
  updateLeaderboardFormulation: (index: number, form: Formulation) => void;
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
  openSettings: (tab?: "llm" | "deps" | "api") => void;
  setSettingsTab: (tab: "llm" | "deps" | "api") => void;

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
  constraints: {},
  levers: [
    { name: "Zinc phosphate", low: 2, high: 14, unit: "wt%" },
    { name: "Bisphenol-A epoxy (DGEBA)", low: 28, high: 48, unit: "wt%" },
    { name: "Polyamide hardener", low: 8, high: 22, unit: "wt%" },
    { name: "cure_temperature_c", low: 50, high: 80, unit: "C" },
  ],
};

let autosaveTimer: ReturnType<typeof setTimeout> | null = null;
const AUTOSAVE_MS = 1500;

function objectiveTargetFromRequirement(
  req: Requirement,
  metric: string
): number | null {
  if (metric === "salt_spray_hours") return req.salt_spray_hours;
  if (metric === "film_weight_gsm" || metric === "coating_weight_gsm") {
    return req.film_weight_gsm;
  }
  if (metric === "cleaning_efficiency") return req.cleaning_efficiency;
  return null;
}

function applyPatchToDraft(draft: AppState, patch: Partial<StoreWorkspaceSlice>): void {
  if (patch.searchQuery !== undefined) draft.searchQuery = patch.searchQuery;
  if (patch.sourceTypes !== undefined) draft.sourceTypes = patch.sourceTypes;
  if (patch.sources !== undefined) draft.sources = patch.sources;
  if (patch.selectedSources !== undefined) draft.selectedSources = patch.selectedSources;
  if (patch.chatHistory !== undefined) draft.chatHistory = patch.chatHistory;
  if (patch.deepReport !== undefined) draft.deepReport = patch.deepReport;
  if (patch.requirement !== undefined) draft.requirement = patch.requirement;
  if (patch.activeConstraints !== undefined) draft.activeConstraints = patch.activeConstraints;
  if (patch.research !== undefined) draft.research = patch.research;
  if (patch.leaderboard !== undefined) draft.leaderboard = patch.leaderboard;
  if (patch.doePlan !== undefined) draft.doePlan = patch.doePlan;
  if (patch.measured !== undefined) draft.measured = patch.measured;
  if (patch.models !== undefined) draft.models = patch.models;
  if (patch.modelHistory !== undefined) draft.modelHistory = patch.modelHistory;
  if (patch.trainMessage !== undefined) draft.trainMessage = patch.trainMessage;
  if (patch.campaignState !== undefined) draft.campaignState = patch.campaignState;
  if (patch.workbenchCampaignId !== undefined) {
    draft.workbenchCampaignId = patch.workbenchCampaignId;
  }
  if (patch.workbenchObjectivesSnapshot !== undefined) {
    draft.workbenchObjectivesSnapshot = patch.workbenchObjectivesSnapshot;
  }
  if (patch.optimizationHistory !== undefined) {
    draft.optimizationHistory = patch.optimizationHistory;
  }
  if (patch.loopReport !== undefined) draft.loopReport = patch.loopReport;
  if (patch.rmseHistory !== undefined) draft.rmseHistory = patch.rmseHistory;
  if (patch.processOptResult !== undefined) draft.processOptResult = patch.processOptResult;
  if (patch.doeEngine !== undefined) draft.doeEngine = patch.doeEngine;
  if (patch.alEngine !== undefined) draft.alEngine = patch.alEngine;
  if (patch.optimizeEngine !== undefined) draft.optimizeEngine = patch.optimizeEngine;
  if (patch.loopDoeEngine !== undefined) draft.loopDoeEngine = patch.loopDoeEngine;
  if (patch.recommendSourceTypes !== undefined) {
    draft.recommendSourceTypes = patch.recommendSourceTypes;
  }
  if (patch.lastAlEngine !== undefined) draft.lastAlEngine = patch.lastAlEngine;
}

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
    immer((set, get) => ({
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
      requirementLocked: false,
      historyOpen: false,

      // v0.3 initial state
      searchQuery: "",
      sourceTypes: ["patents", "literature", "internet"] as SearchSourceType[],
      sources: [],
      selectedSources: [],
      sourceStatus: {} as Record<string, SourceStatus>,
      chatHistory: [],
      searchBusy: false,
      searchProgress: null,
      deepResearchBusy: false,
      deepResearchStage: "",
      deepResearchMessage: "",
      formulationBusy: false,
      recommendStage: "",
      recommendMessage: "",
      chatBusy: false,
      recommendSourceTypes: ["patents", "literature", "internet"] as SearchSourceType[],
      openModal: null,
      activeConstraints: defaultConstraintsForDomain("anticorrosion_coating"),
      llmConfig: { provider: "anthropic", model: "claude-sonnet-4-6" },
      settingsOpen: false,
      settingsTab: "llm",

      // v0.6 initial state
      loopReport: null,
      rmseHistory: [],
      intentBusy: false,

      setField: (key, value) => {
        set((d) => {
          d.requirement[key] = value;
        });
        get().scheduleAutosave();
      },

      setDomain: (d) => {
        set((draft) => {
          draft.requirement.domain = d;
          if (!draft.requirement.objectives.length) {
            draft.requirement.objectives = [...DOMAIN_OBJECTIVES[d]];
          }
        });
        get().scheduleAutosave();
      },

      setLevers: (levers) => {
        set((draft) => {
          draft.requirement.levers = levers;
        });
        get().scheduleAutosave();
      },

      loadExampleProject: async (exampleId) => {
        set((draft) => {
          draft.error = null;
        });
        try {
          const req = await api.loadExampleProject(exampleId);
          set((draft) => {
            draft.requirement = req;
            draft.activeConstraints = defaultConstraintsForDomain(req.domain);
            draft.research = null;
            draft.leaderboard = [];
            draft.doePlan = null;
            draft.measured = {};
          });
          get().scheduleAutosave();
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        }
      },

      setObjectives: (objectives) => {
        set((draft) => {
          draft.requirement.objectives = objectives;
        });
        get().scheduleAutosave();
      },

      updateObjective: (idx, patch) => {
        set((draft) => {
          const obj = draft.requirement.objectives[idx];
          if (!obj) return;
          const merged = normalizeObjective({ ...obj, ...patch });
          Object.assign(obj, merged);
        });
        get().scheduleAutosave();
      },

      removeObjective: (idx) => {
        set((draft) => {
          draft.requirement.objectives.splice(idx, 1);
        });
        get().scheduleAutosave();
      },

      addObjective: (objective) => {
        set((draft) => {
          draft.requirement.objectives.push(normalizeObjective(objective));
        });
        get().scheduleAutosave();
      },

      resetObjectivesForDomain: (domain) => {
        set((draft) => {
          const req = draft.requirement;
          draft.requirement.objectives = normalizeObjectives(
            DOMAIN_OBJECTIVES[domain].map((o) => ({
              ...o,
              target_value:
                objectiveTargetFromRequirement(req, o.metric) ?? o.target_value ?? null,
            }))
          );
        });
        get().scheduleAutosave();
      },

      setActiveConstraints: (keys) => {
        set((draft) => {
          draft.activeConstraints = keys;
        });
        get().scheduleAutosave();
      },

      setConstraintValue: (key, value) => {
        set((draft) => {
          draft.requirement[key] = value;
          if (!draft.requirement.constraints) draft.requirement.constraints = {};
          draft.requirement.constraints[constraintLabelForKey(key)] = value;
        });
        get().scheduleAutosave();
      },

      clearConstraintValue: (key) => {
        set((draft) => {
          if (key === "voc_limit_gpl") draft.requirement.voc_limit_gpl = null;
          else if (key === "cure_temperature_c") draft.requirement.cure_temperature_c = null;
          else if (key === "ph_target") draft.requirement.ph_target = null;
          else draft.requirement[key] = 0;
          if (draft.requirement.constraints) {
            delete draft.requirement.constraints[constraintLabelForKey(key)];
          }
        });
        get().scheduleAutosave();
      },

      setCustomConstraint: (name, value) => {
        const trimmed = name.trim();
        if (!trimmed) return;
        set((draft) => {
          if (!draft.requirement.constraints) draft.requirement.constraints = {};
          draft.requirement.constraints[trimmed] = value;
        });
        get().scheduleAutosave();
      },

      removeCustomConstraint: (name) => {
        set((draft) => {
          if (draft.requirement.constraints) {
            delete draft.requirement.constraints[name];
          }
        });
        get().scheduleAutosave();
      },

      saveRequirementAndRefresh: async () => {
        if (autosaveTimer) {
          clearTimeout(autosaveTimer);
          autosaveTimer = null;
        }
        try {
          await get().saveProject();
        } catch {
          return;
        }
        set((draft) => {
          draft.requirementLocked = true;
        });
        await get().runResearch();
      },

      unlockRequirement: () => {
        set((draft) => {
          draft.requirementLocked = false;
        });
      },

      addManualFormulation: async () => {
        const { requirement } = get();
        const empty: Formulation = {
          name: "手动配方",
          domain: requirement.domain,
          ingredients: [{ name: "新组分", role: "additive", weight_pct: 0 }],
          rationale: "用户手动添加",
          predicted: {},
          predicted_std: {},
          score: null,
          warnings: [],
          source: "manual",
        };
        try {
          const scored = await api.submitManualFormulation(empty, requirement);
          set((draft) => {
            draft.leaderboard = [...draft.leaderboard, scored];
          });
          get().scheduleAutosave();
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        }
      },

      modifyFormulationsWithAi: async (prompt: string) => {
        set((draft) => {
          draft.formulationBusy = true;
          draft.recommendStage = "retrieve";
          draft.recommendMessage = "AI 修改配方中";
          draft.error = null;
        });
        try {
          const { requirement, sources, selectedSources, searchQuery, leaderboard } = get();
          const selected = sources.filter((e) =>
            selectedSources.includes(e.identifier || e.title)
          );
          const payload = selected.length > 0 ? selected : sources;
          const { task_id } = await api.submitModifyResearch(
            requirement,
            prompt,
            leaderboard,
            payload,
            searchQuery.trim()
          );
          const final = await awaitTaskStream(task_id, (ev) => {
            set((draft) => {
              draft.recommendStage = ev.stage ?? "";
              draft.recommendMessage = ev.message ?? "";
              draft.task = progressToTaskStatus(task_id, "recommend", ev);
            });
          });
          const wrapped = final.data as { research?: ResearchResult } | undefined;
          const research = wrapped?.research;
          if (!research) throw new Error("AI 修改未返回结果");
          const newForms = research.recommended.map((f) => ({
            ...f,
            source: "ai_modify",
          }));
          set((draft) => {
            draft.research = research;
            draft.leaderboard = [...draft.leaderboard, ...newForms];
          });
          get().scheduleAutosave();
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        } finally {
          set((draft) => {
            draft.formulationBusy = false;
            draft.recommendStage = "";
            draft.recommendMessage = "";
          });
        }
      },

      updateLeaderboardFormulation: (index, form) => {
        set((draft) => {
          if (draft.leaderboard[index]) {
            draft.leaderboard[index] = form;
          }
        });
        get().scheduleAutosave();
      },

      runResearch: async () => {
        set((draft) => {
          draft.formulationBusy = true;
          draft.recommendStage = "retrieve";
          draft.recommendMessage = "正在检索";
          draft.error = null;
        });
        try {
          const { requirement, sources, selectedSources, searchQuery } = get();
          const selected = sources.filter((e) =>
            selectedSources.includes(e.identifier || e.title)
          );
          const payload = selected.length > 0 ? selected : sources;
          const { task_id } = await api.submitRecommendResearch(
            requirement,
            payload,
            searchQuery.trim()
          );
          const final = await awaitTaskStream(task_id, (ev) => {
            set((draft) => {
              draft.recommendStage = ev.stage ?? "";
              draft.recommendMessage = ev.message ?? "";
              draft.task = progressToTaskStatus(task_id, "recommend", ev);
            });
          });
          const wrapped = final.data as { research?: ResearchResult } | undefined;
          const research = wrapped?.research;
          if (!research) throw new Error("推荐未返回结果");
          set((draft) => {
            draft.research = research;
            draft.leaderboard = research.recommended;
          });
          get().scheduleAutosave();
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        } finally {
          set((draft) => {
            draft.formulationBusy = false;
            draft.recommendStage = "";
            draft.recommendMessage = "";
          });
        }
      },

      runDeepResearch: async () => {
        const { searchQuery, requirement, sources } = get();
        set((draft) => {
          draft.deepResearchBusy = true;
          draft.deepResearchStage = "retrieve";
          draft.deepResearchMessage = "正在检索";
          draft.error = null;
        });
        try {
          const { task_id } = await api.submitDeepResearch(
            searchQuery,
            requirement,
            sources,
            searchQuery.trim()
          );
          const final = await awaitTaskStream(task_id, (ev) => {
            set((draft) => {
              draft.deepResearchStage = ev.stage ?? "";
              draft.deepResearchMessage = ev.message ?? "";
              draft.task = progressToTaskStatus(task_id, "deep_research", ev);
            });
          });
          const wrapped = final.data as { report?: ComprehensiveReport } | undefined;
          const report = wrapped?.report;
          if (!report) throw new Error("深度研究未返回结果");
          set((draft) => {
            draft.deepReport = report;
          });
          if (report.citations?.length) get().addSources(report.citations);
          if (report.candidates?.length) {
            set((draft) => {
              draft.leaderboard = report.candidates!;
            });
          }
          const msg: ChatMessage = {
            role: "assistant",
            content: report.report_markdown,
            citations: report.citations,
          };
          set((draft) => {
            draft.chatHistory.push(msg);
          });
          get().scheduleAutosave();
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        } finally {
          set((draft) => {
            draft.deepResearchBusy = false;
            draft.deepResearchStage = "";
            draft.deepResearchMessage = "";
          });
        }
      },

      refreshKnowledgeBase: async () => {
        const query = get().searchQuery.trim();
        if (!query) {
          set((draft) => {
            draft.error = "请先输入研究主题";
          });
          return;
        }
        set((draft) => {
          draft.searchBusy = true;
          draft.error = null;
        });
        try {
          const res = await api.refreshKnowledgeBase(query);
          set((draft) => {
            draft.deepResearchMessage = `已入库 ${res.fetched} 条（索引共 ${res.indexed_total}）`;
          });
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        } finally {
          set((draft) => {
            draft.searchBusy = false;
          });
        }
      },

      runOptimize: async () => {
        set((draft) => {
          draft.busy = "optimizing";
          draft.error = null;
        });
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
            set((draft) => {
              draft.task = progressToTaskStatus(task_id, "optimize", ev);
            })
          );
          const opt = final.data as unknown as OptimizationResult | null;
          if (opt?.top_formulations) {
            set((draft) => {
              draft.leaderboard = opt.top_formulations;
              draft.optimizationHistory = opt.history;
            });
            get().scheduleAutosave();
          }
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        } finally {
          set((draft) => {
            draft.busy = "idle";
          });
        }
      },

      runLoop: async () => {
        set((draft) => {
          draft.busy = "looping";
          draft.error = null;
        });
        try {
          const { requirement, optimizeEngine, loopDoeEngine } = get();
          const { task_id } = await api.loopIterate(requirement, 24, 4, optimizeEngine, loopDoeEngine);
          const final = await awaitTaskStream(task_id, (ev) =>
            set((draft) => {
              draft.task = progressToTaskStatus(task_id, "loop", ev);
            })
          );
          const report = final.data as unknown as LoopReport | null;
          if (report) {
            set((draft) => {
              draft.loopReport = report;
              draft.leaderboard = report.optimization.top_formulations;
              draft.optimizationHistory = report.optimization.history;
              draft.doePlan = report.next_doe;
              draft.measured = {};
              draft.models = report.model_info;
              draft.rmseHistory.push(report.rmse_by_metric);
              draft.lastAlEngine = report.engine;
            });
            get().scheduleAutosave();
          }
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        } finally {
          set((draft) => {
            draft.busy = "idle";
          });
        }
      },

      applyIntent: async (text) => {
        set((draft) => {
          draft.intentBusy = true;
          draft.error = null;
        });
        try {
          const result = await api.parseIntent(text);
          set((draft) => {
            const r = result.requirement;
            const req = draft.requirement;
            if (r.project_id !== undefined) req.project_id = r.project_id;
            if (r.product_type !== undefined) req.product_type = r.product_type;
            if (r.application !== undefined) req.application = r.application;
            if (r.domain !== undefined) req.domain = r.domain;
            if (r.substrate !== undefined) req.substrate = r.substrate;
            if (r.salt_spray_hours !== undefined) req.salt_spray_hours = r.salt_spray_hours;
            if (r.film_weight_gsm !== undefined) req.film_weight_gsm = r.film_weight_gsm;
            if (r.cure_temperature_c !== undefined) req.cure_temperature_c = r.cure_temperature_c;
            if (r.cleaning_efficiency !== undefined) {
              req.cleaning_efficiency = r.cleaning_efficiency;
            }
            if (r.voc_limit_gpl !== undefined) req.voc_limit_gpl = r.voc_limit_gpl;
            if (r.ph_target !== undefined) req.ph_target = r.ph_target;
            if (r.notes !== undefined) req.notes = r.notes;
            if (r.materials !== undefined) req.materials = r.materials;
            if (r.constraints !== undefined) req.constraints = r.constraints;
            if (r.objectives?.length) req.objectives = r.objectives;
            if (r.levers?.length) req.levers = r.levers;
          });
          get().scheduleAutosave();
          return result.extracted_fields;
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
          return [];
        } finally {
          set((draft) => {
            draft.intentBusy = false;
          });
        }
      },

      generateDoe: async (design) => {
        set((draft) => {
          draft.busy = "doe";
          draft.error = null;
        });
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
          set((draft) => {
            draft.doePlan = plan;
            draft.measured = {};
            draft.campaignState = nextCampaignState;
            draft.lastAlEngine = nextAlEngine;
            draft.workbenchCampaignId = wb.campaign_id;
            draft.workbenchObjectivesSnapshot = wb.objectives_snapshot ?? null;
          });
          await get().refreshWorkbenchStats();
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        } finally {
          set((draft) => {
            draft.busy = "idle";
          });
          get().scheduleAutosave();
        }
      },

      setDoeEngine: (engine) => {
        set((draft) => {
          draft.doeEngine = engine;
        });
        get().scheduleAutosave();
      },
      setAlEngine: (engine) => {
        set((draft) => {
          draft.alEngine = engine;
        });
        get().scheduleAutosave();
      },
      setOptimizeEngine: (engine) => {
        set((draft) => {
          draft.optimizeEngine = engine;
        });
        get().scheduleAutosave();
      },
      setLoopDoeEngine: (engine) => {
        set((draft) => {
          draft.loopDoeEngine = engine;
        });
        get().scheduleAutosave();
      },

      setMeasured: (runId, value) => {
        set((draft) => {
          draft.measured[runId] = value;
        });
        get().scheduleAutosave();
      },

      refreshWorkbenchStats: async () => {
        const id = get().workbenchCampaignId;
        if (id == null) {
          set((draft) => {
            draft.workbenchStats = null;
          });
          return;
        }
        try {
          const wb = await api.getWorkbenchCampaign(id);
          const completed = wb.rows.filter((r) => r.status === "Completed").length;
          set((draft) => {
            draft.workbenchStats = {
              completed,
              total: wb.rows.length,
              name: wb.name,
              strategy: wb.strategy,
            };
            draft.workbenchObjectivesSnapshot =
              wb.objectives_snapshot ?? draft.workbenchObjectivesSnapshot;
          });
        } catch {
          set((draft) => {
            draft.workbenchStats = null;
          });
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
          set((draft) => {
            draft.workbenchCampaignId = wb.campaign_id;
            draft.workbenchObjectivesSnapshot = wb.objectives_snapshot ?? null;
            draft.workbenchStats = {
              completed: 0,
              total: wb.rows.length,
              name: wb.name,
              strategy: wb.strategy,
            };
          });
          get().scheduleAutosave();
          return wb.campaign_id;
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
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
            set((draft) => {
              draft.error = String(e);
            });
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
          set((draft) => {
            draft.error = "请先在实验台账中完成至少一行实测值，或为实验填写实测值";
          });
          return;
        }
        set((draft) => {
          draft.busy = "training";
          draft.error = null;
        });
        try {
          const report = await api.submitExperiments(records);
          set((draft) => {
            draft.models = report.trained;
            draft.modelHistory.push(report.trained);
            draft.trainMessage = report.message;
          });
          await get().runResearch();
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        } finally {
          set((draft) => {
            draft.busy = "idle";
          });
        }
      },

      refreshModels: async () => {
        try {
          const models = await api.models();
          set((draft) => {
            draft.models = models;
          });
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        }
      },

      exportDoe: (format) => {
        const { doePlan } = get();
        if (!doePlan?.plan_id) {
          set((draft) => {
            draft.error = "请先生成 DOE 计划再导出";
          });
          return;
        }
        window.open(api.doeExportUrl(doePlan.plan_id, format), "_blank");
      },

      importCsv: async (file) => {
        set((draft) => {
          draft.busy = "training";
          draft.error = null;
        });
        try {
          const report = await api.importExperimentsCsv(file, get().requirement.domain);
          set((draft) => {
            draft.models = report.trained;
            draft.modelHistory.push(report.trained);
            draft.trainMessage = report.message;
          });
          await get().runResearch();
        } catch (e) {
          set((draft) => {
            draft.error = `CSV 导入失败：${e instanceof Error ? e.message : String(e)}`;
          });
        } finally {
          set((draft) => {
            draft.busy = "idle";
          });
        }
      },

      toggleHistory: () =>
        set((draft) => {
          draft.historyOpen = !draft.historyOpen;
        }),

      scheduleAutosave: () => {
        if (autosaveTimer) clearTimeout(autosaveTimer);
        autosaveTimer = setTimeout(() => {
          void get().saveProject();
        }, AUTOSAVE_MS);
      },

      saveProject: async () => {
        const { activeProjectId } = get();
        if (!activeProjectId) return;
        set((draft) => {
          draft.projectSaveBusy = true;
        });
        try {
          const payload = buildWorkspacePayload(workspaceSlice(get()));
          const title = get().searchQuery.trim() || get().requirement.product_type || undefined;
          await api.updateProject(activeProjectId, payload, title);
          const projects = await api.listProjects();
          set((draft) => {
            draft.projects = projects;
          });
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        } finally {
          set((draft) => {
            draft.projectSaveBusy = false;
          });
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
          set((draft) => {
            applyPatchToDraft(draft, patch);
            draft.activeProjectId = id;
            draft.historyOpen = false;
            draft.error = null;
            draft.task = null;
            draft.busy = "idle";
          });
          if (patch.workbenchCampaignId != null) {
            await get().refreshWorkbenchStats();
          } else {
            set((draft) => {
              draft.workbenchStats = null;
            });
          }
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        }
      },

      createProject: async (title = "") => {
        try {
          await get().saveProject();
          const detail = await api.createProject(title);
          const patch = applyWorkspacePayload(detail.workspace, defaultRequirement);
          set((draft) => {
            applyPatchToDraft(draft, patch);
            draft.searchQuery = title || "";
            draft.activeProjectId = detail.id;
            draft.research = null;
            draft.deepReport = null;
            draft.leaderboard = [];
            draft.chatHistory = [];
            draft.sources = [];
            draft.selectedSources = [];
            draft.doePlan = null;
            draft.measured = {};
            draft.loopReport = null;
            draft.rmseHistory = [];
            draft.processOptResult = null;
            draft.optimizationHistory = [];
            draft.modelHistory = [];
            draft.trainMessage = "";
            draft.campaignState = null;
            draft.workbenchCampaignId = null;
            draft.workbenchObjectivesSnapshot = null;
            draft.workbenchStats = null;
            draft.error = null;
          });
          const projects = await api.listProjects();
          set((draft) => {
            draft.projects = projects;
          });
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
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
            set((draft) => {
              draft.activeProjectId = null;
            });
            if (projects.length > 0) {
              await get().loadProject(projects[0].id);
            } else {
              await get().createProject();
            }
          } else {
            set((draft) => {
              draft.projects = projects;
            });
          }
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
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
          set((draft) => {
            draft.projects = projects;
            draft.activeProjectId = activeId;
          });
          if (activeId) {
            const detail = await api.getProject(activeId);
            const patch = applyWorkspacePayload(detail.workspace, defaultRequirement);
            if (!patch.activeConstraints?.length && patch.requirement) {
              patch.activeConstraints = defaultConstraintsForDomain(patch.requirement.domain);
            }
            set((draft) => {
              applyPatchToDraft(draft, patch);
              draft.activeProjectId = activeId;
            });
            if (patch.workbenchCampaignId != null) {
              await get().refreshWorkbenchStats();
            }
          }
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        }
      },

      setProcessOptResult: (result) => {
        set((draft) => {
          draft.processOptResult = result;
        });
        get().scheduleAutosave();
      },

      // v0.3 actions
      setSearchQuery: (q) => {
        set((draft) => {
          draft.searchQuery = q;
        });
        get().scheduleAutosave();
      },

      setSourceTypes: (types) => {
        set((draft) => {
          draft.sourceTypes = types;
        });
        get().scheduleAutosave();
      },

      setRecommendSourceTypes: (types) => {
        set((draft) => {
          draft.recommendSourceTypes = types;
        });
        get().scheduleAutosave();
      },

      addSources: (evidence) => {
        set((draft) => {
          const fresh = evidence.filter(
            (e) =>
              !draft.sources.some(
                (x) => (x.identifier || x.title) === (e.identifier || e.title)
              )
          );
          const freshIds = fresh.map((e) => e.identifier || e.title);
          draft.sources.push(...fresh);
          for (const id of freshIds) {
            if (!draft.selectedSources.includes(id)) {
              draft.selectedSources.push(id);
            }
          }
        });
        get().scheduleAutosave();
      },

      removeSource: (id) => {
        set((draft) => {
          draft.sources = draft.sources.filter((e) => (e.identifier || e.title) !== id);
          draft.selectedSources = draft.selectedSources.filter((x) => x !== id);
        });
        get().scheduleAutosave();
      },

      clearSources: () => {
        set((draft) => {
          draft.sources = [];
          draft.selectedSources = [];
          draft.chatHistory = [];
        });
        get().scheduleAutosave();
      },

      toggleSourceSelected: (id) => {
        set((draft) => {
          if (draft.selectedSources.includes(id)) {
            draft.selectedSources = draft.selectedSources.filter((x) => x !== id);
          } else {
            draft.selectedSources.push(id);
          }
        });
        get().scheduleAutosave();
      },

      selectAllSources: () =>
        set((draft) => {
          draft.selectedSources = draft.sources.map((e) => e.identifier || e.title);
        }),

      deselectAllSources: () =>
        set((draft) => {
          draft.selectedSources = [];
        }),

      searchSources: async (queryOverride?: string) => {
        const { searchQuery, requirement, sourceTypes } = get();
        const query = (queryOverride ?? searchQuery).trim();
        if (queryOverride !== undefined) {
          set((draft) => {
            draft.searchQuery = query;
          });
        }
        set((draft) => {
          draft.searchBusy = true;
          draft.error = null;
          draft.sources = [];
          draft.selectedSources = [];
          draft.searchProgress = {
            message: "正在排队…",
            total: 0,
            source: null,
            newCount: 0,
            sourcesDone: [],
            sourcesPending: [],
          };
        });
        const types = sourceTypes.filter((t) => t !== "local");
        try {
          const { task_id } = await api.searchStream({
            query,
            requirement,
            source_types: types.length ? types : undefined,
            total_limit: 300,
          });
          const final = await awaitTaskStream(
            task_id,
            (ev) => {
              const { evidence, progress } = parseSearchStreamData(
                ev.data as Record<string, unknown> | undefined
              );
              set((draft) => {
                draft.searchProgress = {
                  message: ev.message || draft.searchProgress?.message || "检索中…",
                  total: progress.total ?? draft.searchProgress?.total ?? 0,
                  source: progress.source ?? null,
                  newCount: progress.newCount ?? 0,
                  sourcesDone: progress.sourcesDone ?? [],
                  sourcesPending: progress.sourcesPending ?? [],
                };
              });
              if (evidence.length) get().addSources(evidence);
            },
            300_000
          );
          const r = final.data as
            | { evidence?: Evidence[]; source_status?: Record<string, SourceStatus> }
            | undefined;
          if (r?.evidence?.length) get().addSources(r.evidence);
          if (r?.source_status) {
            set((draft) => {
              draft.sourceStatus = r.source_status!;
            });
          }
          set((draft) => {
            draft.searchProgress = draft.searchProgress
              ? {
                  ...draft.searchProgress,
                  message: final.message || `检索完成，共 ${draft.sources.length} 条`,
                }
              : null;
          });
          get().scheduleAutosave();
        } catch (e) {
          set((draft) => {
            draft.error = String(e);
          });
        } finally {
          set((draft) => {
            draft.searchBusy = false;
            draft.searchProgress = null;
          });
        }
      },

      loadSourceStatus: async () => {
        try {
          const status = await api.getSourceStatus();
          set((draft) => {
            draft.sourceStatus = status;
          });
        } catch {
          // silently ignore
        }
      },

      hydrateLlmSettings: async () => {
        try {
          const remote = await api.getSettings();
          const local = get().llmConfig;
          set((draft) => {
            draft.llmConfig.provider = remote.provider || local.provider;
            draft.llmConfig.model = remote.model || local.model;
            draft.llmConfig.baseUrl = remote.base_url ?? local.baseUrl;
          });
        } catch {
          // offline — keep persisted provider/model
        }
      },

      uploadFiles: async (files) => {
        if (files.length === 0) return;
        set((draft) => {
          draft.searchBusy = true;
          draft.error = null;
        });
        try {
          const res =
            files.length === 1
              ? await api.ingest(files[0])
              : await api.ingestBatch(files);
          get().addSources(res.evidence);
        } catch (e) {
          set((draft) => {
            draft.error = `文件上传失败：${e instanceof Error ? e.message : String(e)}`;
          });
        } finally {
          set((draft) => {
            draft.searchBusy = false;
          });
        }
      },

      sendChat: async (question) => {
        const { sources, selectedSources, requirement } = get();
        const active = sources.filter((e) =>
          selectedSources.includes(e.identifier || e.title)
        );
        set((draft) => {
          draft.chatBusy = true;
          draft.chatHistory.push({ role: "user", content: question });
        });
        try {
          const res = await api.chat({
            question,
            sources: active,
            domain: requirement.domain,
          });
          set((draft) => {
            draft.chatHistory.push({
              role: "assistant",
              content: res.answer,
              citations: res.citations,
            });
          });
          get().scheduleAutosave();
        } catch (e) {
          set((draft) => {
            draft.chatHistory.push({
              role: "assistant",
              content: `错误：${String(e)}`,
            });
          });
        } finally {
          set((draft) => {
            draft.chatBusy = false;
          });
        }
      },

      setOpenModal: (name) =>
        set((draft) => {
          draft.openModal = name;
        }),

      setLlmConfig: (config) =>
        set((draft) => {
          Object.assign(draft.llmConfig, config);
        }),

      toggleSettings: () =>
        set((draft) => {
          draft.settingsOpen = !draft.settingsOpen;
        }),

      openSettings: (tab = "llm") =>
        set((draft) => {
          draft.settingsOpen = true;
          draft.settingsTab = tab;
        }),

      setSettingsTab: (tab) =>
        set((draft) => {
          draft.settingsTab = tab;
        }),
    })),
    {
      name: "formumind-history",
      partialize: (state) => ({
        activeProjectId: state.activeProjectId,
        llmConfig: {
          provider: state.llmConfig.provider,
          model: state.llmConfig.model,
          baseUrl: state.llmConfig.baseUrl,
        },
      }),
    }
  )
);
