import type {
  ChatMessage,
  ComprehensiveReport,
  DOEPlan,
  Evidence,
  Formulation,
  LeverSpec,
  LLMConfig,
  LoopReport,
  ModelInfo,
  ObjectiveSpec,
  ProcessOptResult,
  ProductDomain,
  Requirement,
  ResearchResult,
  SearchSourceType,
  SearchStreamProgress,
  SourceStatus,
  TaskStatus,
} from "../api";
import type { ConstraintKey } from "../constants/constraints";
import type { ProjectSummary, StoreWorkspaceSlice } from "../projectWorkspace";

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

export type { ProjectSummary } from "../projectWorkspace";

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

export interface AppState {
  requirement: Requirement;
  research: ResearchResult | null;
  deepReport: ComprehensiveReport | null;
  task: TaskStatus | null;
  leaderboard: Formulation[];
  optimizationHistory: number[];
  busy: "idle" | "researching" | "optimizing" | "doe" | "training" | "looping";
  error: string | null;

  loopReport: LoopReport | null;
  rmseHistory: Record<string, number>[];
  intentBusy: boolean;

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
  modelHistory: ModelInfo[][];
  trainMessage: string;

  projects: ProjectSummary[];
  activeProjectId: string | null;
  processOptResult: ProcessOptResult | null;
  projectSaveBusy: boolean;
  requirementLocked: boolean;
  historyOpen: boolean;

  searchQuery: string;
  sourceTypes: SearchSourceType[];
  sources: Evidence[];
  selectedSources: string[];
  sourceStatus: Record<string, SourceStatus>;
  usedSeedFallback: boolean;
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
  openModal: string | null;
  activeConstraints: ConstraintKey[];
  requirementSnapshot: Requirement | null;
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
  syncDefaultLevers: () => Promise<void>;
  loadExampleProject: (exampleId: string) => Promise<void>;
  setActiveConstraints: (keys: ConstraintKey[]) => void;
  setConstraintValue: (key: ConstraintKey, value: number) => void;
  clearConstraintValue: (key: ConstraintKey) => void;
  addCustomConstraint: (name: string, value: number) => void;
  removeCustomConstraint: (name: string) => void;
  updateCustomConstraint: (name: string, value: number) => void;
  captureRequirementSnapshot: () => void;
  resetRequirement: () => void;
  saveRequirementAndRefresh: () => Promise<void>;
  unlockRequirement: () => void;
  setLeaderboard: (forms: Formulation[]) => void;
  addManualFormula: () => Promise<void>;
  updateFormulaIngredient: (
    formulaIdx: number,
    ingIdx: number,
    patch: Partial<import("../api").Ingredient>
  ) => void;
  runAiModifyFormula: (prompt: string, baseIndex?: number) => Promise<void>;
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
  runLoop: () => Promise<void>;
  applyIntent: (text: string) => Promise<string[]>;
}

export type { StoreWorkspaceSlice };
