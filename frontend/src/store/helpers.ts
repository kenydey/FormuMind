import type { Requirement } from "../api";
import type { AppState } from "./types";
import { DOMAIN_OBJECTIVES } from "./types";
import type { StoreWorkspaceSlice } from "../projectWorkspace";

export const defaultRequirement: Requirement = {
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
  constraint_values: {},
  levers: [],
};

export const AUTOSAVE_MS = 1500;

export function objectiveTargetFromRequirement(
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

export function applyPatchToDraft(draft: AppState, patch: Partial<StoreWorkspaceSlice>): void {
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

export function workspaceSlice(state: AppState): StoreWorkspaceSlice {
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
