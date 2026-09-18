import { modalForArtifact, type ArtifactKind } from "../../artifacts/projectArtifacts";
import type { FormulationSkill } from "../../api";
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
      tab: "llm" | "deps" | "api" | "env" | "recommend" | "notebooklm" | "org" = "llm",
      opts?: { focusEnvAttr?: string | null },
    ) =>
      set((draft) => {
        draft.settingsOpen = true;
        draft.settingsTab = tab;
        draft.settingsEnvFocusAttr =
          tab === "env" && opts?.focusEnvAttr ? String(opts.focusEnvAttr) : null;
      }),

    clearSettingsEnvFocus: () =>
      set((draft) => {
        draft.settingsEnvFocusAttr = null;
      }),

    bumpEnvFlagsRevision: () =>
      set((draft) => {
        draft.envFlagsRevision = (draft.envFlagsRevision || 0) + 1;
      }),

    setSettingsTab: (tab: "llm" | "deps" | "api" | "env" | "recommend" | "notebooklm" | "org") =>
      set((draft) => {
        draft.settingsTab = tab;
        if (tab !== "env") draft.settingsEnvFocusAttr = null;
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
      if (typeof presets.search_hint === "string" && presets.search_hint.trim()) {
        get().setSearchQuery(presets.search_hint.trim());
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
    | "toggleArtifactDrawer"
    | "openArtifact"
    | "setActiveArtifactId"
    | "applyFormulationSkill"
    | "clearFormulationSkill"
  >;
}
