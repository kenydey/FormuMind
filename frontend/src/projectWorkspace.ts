/**
 * Serialize / deserialize project workspace between Zustand store and backend API (snake_case).
 */
import type { ConstraintKey } from "./constants/constraints";
import type {
  ChatMessage,
  ComprehensiveReport,
  DOEPlan,
  Evidence,
  Formulation,
  LoopReport,
  ModelInfo,
  ObjectiveSpec,
  ProcessOptResult,
  Requirement,
  ResearchResult,
  SearchSourceType,
} from "./api";

const SOURCE_LIMIT = 300;

/** Merge deprecated constraints dict into constraint_values when loading old projects. */
export function migrateRequirementConstraints(req: Requirement): Requirement {
  const legacy = req.constraints;
  if (!legacy || !Object.keys(legacy).length) {
    const { constraints: _c, ...rest } = req;
    return rest as Requirement;
  }
  const cv = { ...(req.constraint_values ?? {}) };
  for (const [k, v] of Object.entries(legacy)) {
    if (v != null && !(k in cv)) cv[k] = v;
  }
  const { constraints: _c, ...rest } = req;
  return { ...rest, constraint_values: cv };
}

export interface ProjectSummary {
  id: string;
  title: string;
  headline: string;
  domain: string;
  created_at: string;
  updated_at: string;
  source_count: number;
  chat_count: number;
  leaderboard_count: number;
  has_doe: boolean;
  has_optimize: boolean;
  has_loop: boolean;
  has_process_opt: boolean;
}

export interface ProjectWorkspacePayload {
  search_query: string;
  source_types: SearchSourceType[];
  sources: Evidence[];
  selected_sources: string[];
  chat_history: ChatMessage[];
  deep_report: ComprehensiveReport | null;
  requirement: Requirement | null;
  active_constraints: ConstraintKey[];
  research: ResearchResult | null;
  leaderboard: Formulation[];
  doe_plan: DOEPlan | null;
  measured: Record<string, number>;
  models: ModelInfo[];
  model_history: ModelInfo[][];
  train_message: string;
  campaign_state: string | null;
  workbench_campaign_id: number | null;
  workbench_adopted_plan_id?: string | null;
  workbench_objectives_snapshot: ObjectiveSpec[];
  optimization_history: number[];
  loop_report: LoopReport | null;
  rmse_history: Record<string, number>[];
  process_opt_result: ProcessOptResult | null;
  doe_engine: string;
  al_engine: string;
  optimize_engine: string;
  loop_doe_engine: string;
  recommend_source_types: SearchSourceType[];
  last_al_engine: string | null;
  auto_loop_on_sync?: boolean;
}

export interface StoreWorkspaceSlice {
  searchQuery: string;
  sourceTypes: SearchSourceType[];
  sources: Evidence[];
  selectedSources: string[];
  chatHistory: ChatMessage[];
  deepReport: ComprehensiveReport | null;
  requirement: Requirement;
  activeConstraints: ConstraintKey[];
  research: ResearchResult | null;
  leaderboard: Formulation[];
  doePlan: DOEPlan | null;
  measured: Record<number, number>;
  models: ModelInfo[];
  modelHistory: ModelInfo[][];
  trainMessage: string;
  campaignState: string | null;
  workbenchCampaignId: number | null;
  workbenchAdoptedPlanId: string | null;
  workbenchObjectivesSnapshot: ObjectiveSpec[] | null;
  optimizationHistory: number[];
  loopReport: LoopReport | null;
  rmseHistory: Record<string, number>[];
  processOptResult: ProcessOptResult | null;
  doeEngine: "auto" | "native" | "pydoe";
  alEngine: "auto" | "legacy" | "baybe";
  optimizeEngine: "auto" | "baybe" | "legacy";
  loopDoeEngine: "auto" | "legacy" | "baybe";
  recommendSourceTypes: SearchSourceType[];
  lastAlEngine: string | null;
  autoLoopOnSync: boolean;
}

export function buildWorkspacePayload(slice: StoreWorkspaceSlice): ProjectWorkspacePayload {
  const measured: Record<string, number> = {};
  for (const [k, v] of Object.entries(slice.measured)) {
    if (v !== undefined && !Number.isNaN(v)) measured[String(k)] = v;
  }
  return {
    search_query: slice.searchQuery,
    source_types: slice.sourceTypes,
    sources: slice.sources,
    selected_sources: slice.selectedSources,
    chat_history: slice.chatHistory,
    deep_report: slice.deepReport,
    requirement: slice.requirement,
    active_constraints: slice.activeConstraints,
    research: slice.research,
    leaderboard: slice.leaderboard,
    doe_plan: slice.doePlan,
    measured,
    models: slice.models,
    model_history: slice.modelHistory,
    train_message: slice.trainMessage,
    campaign_state: slice.campaignState,
    workbench_campaign_id: slice.workbenchCampaignId,
    workbench_adopted_plan_id: slice.workbenchAdoptedPlanId,
    workbench_objectives_snapshot: slice.workbenchObjectivesSnapshot ?? [],
    optimization_history: slice.optimizationHistory,
    loop_report: slice.loopReport,
    rmse_history: slice.rmseHistory,
    process_opt_result: slice.processOptResult,
    doe_engine: slice.doeEngine,
    al_engine: slice.alEngine,
    optimize_engine: slice.optimizeEngine,
    loop_doe_engine: slice.loopDoeEngine,
    recommend_source_types: slice.recommendSourceTypes,
    last_al_engine: slice.lastAlEngine,
    auto_loop_on_sync: slice.autoLoopOnSync,
  };
}

export function applyWorkspacePayload(
  ws: ProjectWorkspacePayload,
  fallbackRequirement: Requirement
): Partial<StoreWorkspaceSlice> {
  const measured: Record<number, number> = {};
  for (const [k, v] of Object.entries(ws.measured || {})) {
    measured[Number(k)] = v;
  }
  return {
    searchQuery: ws.search_query ?? "",
    sourceTypes: (ws.source_types?.length
      ? ws.source_types
      : ["patents", "literature", "internet"]) as SearchSourceType[],
    sources: ws.sources ?? [],
    selectedSources: ws.selected_sources ?? [],
    chatHistory: ws.chat_history ?? [],
    deepReport: ws.deep_report ?? null,
    requirement: migrateRequirementConstraints(ws.requirement ?? fallbackRequirement),
    activeConstraints: (ws.active_constraints?.length
      ? ws.active_constraints
      : []) as ConstraintKey[],
    research: ws.research ?? null,
    leaderboard: ws.leaderboard ?? [],
    doePlan: ws.doe_plan ?? null,
    measured,
    models: ws.models ?? [],
    modelHistory: ws.model_history ?? [],
    trainMessage: ws.train_message ?? "",
    campaignState: ws.campaign_state ?? null,
    workbenchCampaignId: ws.workbench_campaign_id ?? null,
    workbenchAdoptedPlanId:
      ws.workbench_adopted_plan_id ??
      (ws.workbench_campaign_id && ws.doe_plan?.plan_id ? ws.doe_plan.plan_id : null),
    workbenchObjectivesSnapshot: ws.workbench_objectives_snapshot?.length
      ? ws.workbench_objectives_snapshot
      : null,
    optimizationHistory: ws.optimization_history ?? [],
    loopReport: ws.loop_report ?? null,
    rmseHistory: ws.rmse_history ?? [],
    processOptResult: ws.process_opt_result ?? null,
    doeEngine: (ws.doe_engine as StoreWorkspaceSlice["doeEngine"]) ?? "auto",
    alEngine: (ws.al_engine as StoreWorkspaceSlice["alEngine"]) ?? "auto",
    optimizeEngine: (ws.optimize_engine as StoreWorkspaceSlice["optimizeEngine"]) ?? "auto",
    loopDoeEngine: (ws.loop_doe_engine as StoreWorkspaceSlice["loopDoeEngine"]) ?? "auto",
    recommendSourceTypes: (ws.recommend_source_types?.length
      ? ws.recommend_source_types
      : ["patents", "literature", "internet"]) as SearchSourceType[],
    lastAlEngine: ws.last_al_engine ?? null,
    autoLoopOnSync: ws.auto_loop_on_sync ?? false,
  };
}

export interface LegacySessionSnapshot {
  id: string;
  timestamp: string;
  domain: string;
  headline: string;
  topScore: number | null;
  requirement: Requirement;
  leaderboard: Formulation[];
  models: ModelInfo[];
  optimizationHistory: number[];
}

export function legacySnapshotsFromStorage(): LegacySessionSnapshot[] {
  try {
    const raw = localStorage.getItem("formumind-history");
    if (!raw) return [];
    const parsed = JSON.parse(raw) as { state?: { history?: LegacySessionSnapshot[] } };
    return parsed.state?.history ?? [];
  } catch {
    return [];
  }
}

export function markLegacyMigrated(): void {
  localStorage.setItem("formumind-projects-migrated", "1");
}

export function isLegacyMigrated(): boolean {
  return localStorage.getItem("formumind-projects-migrated") === "1";
}

export { SOURCE_LIMIT };
