import { create } from "zustand";
import { persist } from "zustand/middleware";
import { immer } from "zustand/middleware/immer";
import type { SearchSourceType, SourceStatus } from "../api";
import { defaultConstraintsForDomain } from "../constants/constraints";
import { defaultRequirement } from "./helpers";
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
      optimizationHistory: [],
      busy: "idle",
      error: null,
      doePlan: null,
      measured: {},
      doeEngine: "auto",
      alEngine: "auto",
      optimizeEngine: "auto",
      loopDoeEngine: "auto",
      autoLoopOnSync: false,
      campaignState: null,
      workbenchCampaignId: null,
      workbenchAdoptedPlanId: null,
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
