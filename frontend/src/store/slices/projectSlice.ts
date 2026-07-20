import { api, formatApiError } from "../../api";
import { applyWorkspacePayload, buildWorkspacePayload, isLegacyMigrated, legacySnapshotsFromStorage, markLegacyMigrated } from "../../projectWorkspace";
import { defaultConstraintsForDomain } from "../../constants/constraints";
import { applyPatchToDraft, AUTOSAVE_MS, workspaceSlice, defaultRequirement } from "../helpers";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";

export function createProjectSlice(set: SliceSet, get: SliceGet) {
  let autosaveTimer: ReturnType<typeof setTimeout> | null = null;

  return {
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

    cancelAutosave: () => {
      if (autosaveTimer) {
        clearTimeout(autosaveTimer);
        autosaveTimer = null;
      }
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
          draft.error = formatApiError(e);
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
        if (!get().requirement.levers?.length) {
          await get().syncDefaultLevers();
        }
        if (patch.workbenchCampaignId != null) {
          await get().refreshWorkbenchStats();
        } else {
          set((draft) => {
            draft.workbenchStats = null;
          });
        }
        get().captureRequirementSnapshot();
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
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
          draft.formulationValidateWarnings = [];
          draft.chatHistory = [];
          draft.sources = [];
          draft.selectedSources = [];
          draft.doePlan = null;
          draft.adaptiveDoe = null;
          draft.measured = {};
          draft.loopReport = null;
          draft.rmseHistory = [];
          draft.processOptResult = null;
          draft.optimizationHistory = [];
          draft.modelHistory = [];
          draft.trainMessage = "";
          draft.campaignState = null;
          draft.workbenchCampaignId = null;
          draft.workbenchAdoptedPlanId = null;
          draft.workbenchObjectivesSnapshot = null;
          draft.workbenchStats = null;
          draft.error = null;
        });
        if (!get().requirement.levers?.length) {
          await get().syncDefaultLevers();
        }
        const projects = await api.listProjects();
        set((draft) => {
          draft.projects = projects;
        });
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
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
          draft.error = formatApiError(e);
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
          if (!get().requirement.levers?.length) {
            await get().syncDefaultLevers();
          }
          if (patch.workbenchCampaignId != null) {
            await get().refreshWorkbenchStats();
          }
          get().captureRequirementSnapshot();
        }
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
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
  } as Pick<AppState, 'toggleHistory' | 'scheduleAutosave' | 'cancelAutosave' | 'saveProject' | 'loadProject' | 'createProject' | 'deleteProject' | 'initProjects' | 'setProcessOptResult'>;
}
