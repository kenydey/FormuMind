import { modalForArtifact, type ArtifactKind } from "../../artifacts/projectArtifacts";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";

export function createUiSlice(set: SliceSet, _get: SliceGet) {
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
      }),

    openSettings: (tab: "llm" | "deps" | "api" | "env" | "recommend" | "notebooklm" | "org" = "llm") =>
      set((draft) => {
        draft.settingsOpen = true;
        draft.settingsTab = tab;
      }),

    setSettingsTab: (tab: "llm" | "deps" | "api" | "env" | "recommend" | "notebooklm" | "org") =>
      set((draft) => {
        draft.settingsTab = tab;
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
        const modal = modalForArtifact(id);
        if (modal) draft.openModal = modal;
      }),

    setActiveArtifactId: (id: ArtifactKind | null) =>
      set((draft) => {
        draft.activeArtifactId = id;
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
    | "setSettingsTab"
    | "toggleArtifactDrawer"
    | "openArtifact"
    | "setActiveArtifactId"
  >;
}
