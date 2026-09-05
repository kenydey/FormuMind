import { api, formatApiError } from "../../api";
import { applyWorkspacePayload, buildWorkspacePayload, isLegacyMigrated, legacySnapshotsFromStorage, markLegacyMigrated } from "../../projectWorkspace";
import { defaultConstraintsForDomain } from "../../constants/constraints";
import { applyPatchToDraft, AUTOSAVE_MS, workspaceSlice, defaultRequirement } from "../helpers";
import { noNotificationsDismissed } from "../notifications";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState, StoreWorkspaceSlice } from "../types";

export function createProjectSlice(set: SliceSet, get: SliceGet) {
  let autosaveTimer: ReturnType<typeof setTimeout> | null = null;
  // dirty 去重(2026-09-05): 内容无变化不重复 PUT(降噪 + 防空覆盖)
  let lastSavedJson = "";

  function workspaceJson(s: StoreWorkspaceSlice) {
    const payload = buildWorkspacePayload(s);
    return JSON.stringify({
      sources: payload.sources,
      chat_history: payload.chat_history,
      requirement: payload.requirement,
      leaderboard: payload.leaderboard,
      research: payload.research,
      doe_plan: payload.doe_plan,
      active_constraints: payload.active_constraints,
      measured: payload.measured,
    });
  }

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
      const { activeProjectId, projectLoading } = get();
      // loadProject 未完成时挂起 autosave：此时 workspace state 可能仍为空
      // （persist 不含 sources/chat），立即保存会把空 workspace 覆盖到后端 payload。
      if (projectLoading) return;
      if (!activeProjectId) return;
      // dirty 去重: 内容相对上次保存无变化 → 跳过 PUT(降噪, 防空覆盖)
      const json = workspaceJson(workspaceSlice(get()));
      if (json === lastSavedJson) return;
      set((draft) => {
        draft.projectSaveBusy = true;
      });
      try {
        const payload = buildWorkspacePayload(workspaceSlice(get()));
        const title = get().searchQuery.trim() || get().requirement.product_type || undefined;
        await api.updateProject(activeProjectId, payload, title);
        lastSavedJson = json;
        const projects = await api.listProjects();
        set((draft) => {
          draft.projects = projects;
          // 自动保存成功 ⇒ 后端可达，清除历史「后端不可达」错误，避免瞬时错误的永久显示
          draft.error = null;
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
      set((draft) => {
        draft.projectLoading = true;
      });
      get().cancelAutosave();
      try {
        const { activeProjectId } = get();
        if (activeProjectId && activeProjectId !== id) {
          await get().saveProject();
        }
        const detail = await api.getProject(id);
        const patch = applyWorkspacePayload(detail.workspace, defaultRequirement);
        // 空 payload 保护(2026-09-05): 后端 sources/chat 为空而本地镜像有值
        // → 保留本地显示(防事故空 payload 循环覆盖), 内容以服务端后续为准。
        const prevWs = workspaceSlice(get());
        if (!patch.sources?.length && prevWs.sources?.length) {
          patch.sources = prevWs.sources;
          console.warn("[project] 后端 sources 为空, 已用本地镜像恢复显示 (project %s)", id);
        }
        if (!patch.chatHistory?.length && prevWs.chatHistory?.length) {
          patch.chatHistory = prevWs.chatHistory;
          console.warn("[project] 后端 chat_history 为空, 已用本地镜像恢复显示 (project %s)", id);
        }
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
          // project 切换: 会话归零(旧会话存档在后端, 可经会话列表恢复)
          draft.activeSessionId = null;
          draft.kbIngest = null;
          draft.searchProgress = null;
          draft.notificationsDismissed = noNotificationsDismissed();
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
      } finally {
        set((draft) => {
          draft.projectLoading = false;
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
          draft.activeSessionId = null;
          draft.chatSessions = [];
          draft.chatSessionTitles = {};
          draft.sources = [];
          draft.selectedSources = [];
          draft.doePlan = null;
          draft.adaptiveDoe = null;
          draft.measured = {};
          draft.loopReport = null;
          draft.rmseHistory = [];
          draft.optimizationHistory = [];
          draft.modelHistory = [];
          draft.trainMessage = "";
          draft.campaignState = null;
          draft.workbenchCampaignId = null;
          draft.workbenchAdoptedPlanId = null;
          draft.workbenchObjectivesSnapshot = null;
          draft.workbenchStats = null;
          draft.error = null;
          draft.kbIngest = null;
          draft.searchProgress = null;
          draft.notificationsDismissed = noNotificationsDismissed();
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

    deleteProject: async (id, knowledge) => {
      try {
        const { activeProjectId } = get();
        if (activeProjectId && activeProjectId !== id) {
          await get().saveProject();
        }
        await api.deleteProject(id, knowledge ?? "delete");
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

    // v0.3 actions
  } as Pick<AppState, 'toggleHistory' | 'scheduleAutosave' | 'cancelAutosave' | 'saveProject' | 'loadProject' | 'createProject' | 'deleteProject' | 'initProjects'>;
}
