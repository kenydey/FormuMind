import { modalForArtifact, type ArtifactKind } from "../../artifacts/projectArtifacts";
import type { FormulationSkill } from "../../api";
import { resolveSearchHint } from "../../lib/domainSearchHints";
import { normalizeSettingsTab, isRelocatedSettingsTab } from "../settingsTabs";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";

export function createUiSlice(set: SliceSet, get: SliceGet) {
  return {
    setOpenModal: (name: string | null) =>
      set((draft) => {
        draft.openModal = name;
      }),

    setKnowledgeHubTab: (tab: AppState["knowledgeHubTab"]) =>
      set((draft) => {
        draft.knowledgeHubTab = tab;
      }),

    openKnowledgeHub: (tab: AppState["knowledgeHubTab"] = "materials") =>
      set((draft) => {
        draft.knowledgeHubTab = tab;
        draft.openModal = "knowledge";
      }),

    setPreferMaterialsCatalog: (v: boolean) =>
      set((draft) => {
        draft.preferMaterialsCatalog = Boolean(v);
      }),

    setLlmConfig: (config: Partial<AppState["llmConfig"]>) =>
      set((draft) => {
        Object.assign(draft.llmConfig, config);
      }),

    toggleSettings: () =>
      set((draft) => {
        draft.settingsOpen = !draft.settingsOpen;
        if (!draft.settingsOpen) draft.settingsEnvFocusAttr = null;
      }),

    openSettings: (
      tab: string = "model",
      opts?: { focusEnvAttr?: string | null },
    ) => {
      // Wave 0: 搬出设置页的旧 tab 保留深链 —— 记忆→知识库，项目→历史面板，看板→独立入口。
      if (isRelocatedSettingsTab(tab)) {
        if (tab === "memory") get().openKnowledgeHub("memory");
        else if (tab === "project") {
          if (!get().historyOpen) get().toggleHistory();
        } else get().toggleOrg();
        return;
      }
      const focusAttr = tab === "env" || tab === "advanced" ? opts?.focusEnvAttr : null;
      set((draft) => {
        draft.settingsOpen = true;
        draft.settingsTab = normalizeSettingsTab(tab);
        draft.settingsEnvFocusAttr = focusAttr ? String(focusAttr) : null;
      });
    },

    clearSettingsEnvFocus: () =>
      set((draft) => {
        draft.settingsEnvFocusAttr = null;
      }),

    bumpEnvFlagsRevision: () =>
      set((draft) => {
        draft.envFlagsRevision = (draft.envFlagsRevision || 0) + 1;
      }),

    setChatMode: (mode: "chat" | "evidence") =>
      set((draft) => {
        draft.chatMode = mode;
      }),

    toggleSelectedChatSkill: (id: string) =>
      set((draft) => {
        const setIds = new Set(draft.selectedChatSkills);
        if (setIds.has(id)) setIds.delete(id);
        else setIds.add(id);
        draft.selectedChatSkills = [...setIds];
      }),

    toggleSelectedConnector: (id: string) =>
      set((draft) => {
        const setIds = new Set(draft.selectedConnectors);
        if (setIds.has(id)) setIds.delete(id);
        else setIds.add(id);
        draft.selectedConnectors = [...setIds];
      }),

    toggleSelectedMcpServer: (id: string) =>
      set((draft) => {
        const setIds = new Set(draft.selectedMcpServers);
        if (setIds.has(id)) setIds.delete(id);
        else if (setIds.size < 4) setIds.add(id);
        draft.selectedMcpServers = [...setIds];
      }),

    clearComposerSelections: () =>
      set((draft) => {
        draft.selectedChatSkills = [];
        draft.selectedConnectors = [];
        draft.selectedMcpServers = [];
        draft.chatMode = "chat";
      }),

    setChatDraftAppender: (fn) =>
      set((draft) => {
        draft.chatDraftAppender = fn;
      }),

    appendChatDraftRef: (text: string) => {
      const fn = get().chatDraftAppender;
      if (fn) fn(text);
    },

    setSettingsTab: (tab: string) =>
      set((draft) => {
        draft.settingsTab = normalizeSettingsTab(tab);
        if (draft.settingsTab !== "advanced") draft.settingsEnvFocusAttr = null;
      }),

    orgOpen: false as boolean,
    toggleOrg: () =>
      set((draft) => {
        draft.orgOpen = !draft.orgOpen;
      }),

    toggleArtifactDrawer: () =>
      set((draft) => {
        draft.artifactDrawerOpen = !draft.artifactDrawerOpen;
        if (draft.artifactDrawerOpen) draft.historyOpen = false;
      }),

    openArtifact: (id: ArtifactKind) =>
      set((draft) => {
        draft.activeArtifactId = id;
        draft.artifactDrawerOpen = true;
        draft.historyOpen = false;
        // Dossier Report lives in Knowledge Hub → Reports tab (P5), not a
        // dedicated ActionsPanel modal.
        if (id === "wiki_report") {
          draft.knowledgeHubTab = "reports";
          draft.openModal = "knowledge";
          return;
        }
        const modal = modalForArtifact(id);
        if (modal) draft.openModal = modal;
      }),

    setActiveArtifactId: (id: ArtifactKind | null) =>
      set((draft) => {
        draft.activeArtifactId = id;
      }),

    applyFormulationSkill: (skill: FormulationSkill) => {
      set((draft) => {
        draft.activeSkillId = skill.id;
        draft.activeSkill = skill;
        const design = skill.presets?.doe_design;
        draft.pendingDoeDesign = typeof design === "string" ? design : null;
      });
      const presets = skill.presets || {};
      if (typeof presets.prefer_materials_catalog === "boolean") {
        get().setPreferMaterialsCatalog(presets.prefer_materials_catalog);
      }
      if (
        presets.doe_engine === "auto" ||
        presets.doe_engine === "native" ||
        presets.doe_engine === "pydoe"
      ) {
        get().setDoeEngine(presets.doe_engine);
      }
      if (
        presets.optimize_engine === "auto" ||
        presets.optimize_engine === "baybe" ||
        presets.optimize_engine === "legacy"
      ) {
        get().setOptimizeEngine(presets.optimize_engine);
      }
      {
        const hint = resolveSearchHint(presets, get().requirement?.domain);
        if (hint) get().setSearchQuery(hint);
      }
      if (skill.modal) {
        get().setOpenModal(skill.modal);
      }
    },

    clearFormulationSkill: () =>
      set((draft) => {
        draft.activeSkillId = null;
        draft.activeSkill = null;
        draft.pendingDoeDesign = null;
      }),
  } as Pick<
    AppState,
    | "setOpenModal"
    | "setKnowledgeHubTab"
    | "openKnowledgeHub"
    | "setPreferMaterialsCatalog"
    | "setLlmConfig"
    | "toggleSettings"
    | "openSettings"
    | "clearSettingsEnvFocus"
    | "bumpEnvFlagsRevision"
    | "setSettingsTab"
    | "orgOpen"
    | "toggleOrg"
    | "setChatMode"
    | "toggleSelectedChatSkill"
    | "toggleSelectedConnector"
    | "toggleSelectedMcpServer"
    | "clearComposerSelections"
    | "setChatDraftAppender"
    | "appendChatDraftRef"
    | "toggleArtifactDrawer"
    | "openArtifact"
    | "setActiveArtifactId"
    | "applyFormulationSkill"
    | "clearFormulationSkill"
  >;
}
