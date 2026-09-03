import { create } from "zustand";
import { persist } from "zustand/middleware";
import { immer } from "zustand/middleware/immer";
import type { SearchSourceType, SourceStatus } from "../api";
import { defaultConstraintsForDomain } from "../constants/constraints";
import { defaultRequirement } from "./helpers";
import { noNotificationsDismissed } from "./notifications";
import { createNotificationSlice } from "./slices/notificationSlice";
import { createProjectSlice } from "./slices/projectSlice";
import { createRequirementSlice } from "./slices/requirementSlice";
import { createResearchSlice } from "./slices/researchSlice";
import { createSearchSlice } from "./slices/searchSlice";
import { createUiSlice } from "./slices/uiSlice";
import { createWorkflowSlice } from "./slices/workflowSlice";
import type { AppState } from "./types";

export { DOMAIN_OBJECTIVES } from "./types";
export type { AppState, ProjectSummary, SessionSnapshot } from "./types";

export const useStore = create<AppState>()(
  persist(
    immer((set, get) => ({
      requirement: defaultRequirement,
      research: null,
      deepReport: null,
      task: null,
      leaderboard: [],
      formulationValidateWarnings: [],
      optimizationHistory: [],
      busy: "idle",
      error: null,
      doePlan: null,
      adaptiveDoe: null,
      measured: {},
      doeEngine: "auto",
      alEngine: "auto",
      optimizeEngine: "auto",
      loopDoeEngine: "auto",
      autoLoopOnSync: false,
      autoLoopMaxRounds: 5,
      autoLoopRound: 0,
      campaignState: null,
      workbenchCampaignId: null,
      workbenchAdoptedPlanId: null,
      workbenchObjectivesSnapshot: null,
      workbenchStats: null,
      lastAlEngine: null,
      models: [],
      modelHistory: [],
      trainMessage: "",
      trainingStatus: null,
      projects: [],
      activeProjectId: null,
      projectSaveBusy: false,
      projectLoading: false,
      requirementLocked: false,
      historyOpen: false,
      searchQuery: "",
      sourceTypes: ["patents", "literature", "internet"] as SearchSourceType[],
      sources: [],
      selectedSources: [],
      sourceStatus: {} as Record<string, SourceStatus>,
      usedSeedFallback: false,
      filterReport: null,
      chatHistory: [],
      searchBusy: false,
      searchProgress: null,
      kbIngest: null,
      notificationsDismissed: noNotificationsDismissed(),
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
      requirementSnapshot: null,
      llmConfig: { provider: "anthropic", model: "claude-sonnet-4-6" },
      settingsOpen: false,
      settingsTab: "llm",
      loopReport: null,
      rmseHistory: [],
      intentBusy: false,

      ...createRequirementSlice(set, get),
      ...createResearchSlice(set, get),
      ...createWorkflowSlice(set, get),
      ...createProjectSlice(set, get),
      ...createSearchSlice(set, get),
      ...createUiSlice(set, get),
      ...createNotificationSlice(set, get),
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
        // Local backup — protects against API save failures
        leaderboard: (state.leaderboard ?? []).slice(0, 20),
        doePlan: state.doePlan,
        requirement: state.requirement,
        optimizationHistory: state.optimizationHistory,
        models: state.models,
        loopReport: state.loopReport,
        adaptiveDoe: state.adaptiveDoe,
        research: state.research,
        deepReport: state.deepReport,
        measured: state.measured,
        modelHistory: state.modelHistory,
        trainMessage: state.trainMessage,
        campaignState: state.campaignState,
        workbenchCampaignId: state.workbenchCampaignId,
        workbenchAdoptedPlanId: state.workbenchAdoptedPlanId,
        workbenchObjectivesSnapshot: state.workbenchObjectivesSnapshot,
        rmseHistory: state.rmseHistory,
      }),
    }
  )
);
