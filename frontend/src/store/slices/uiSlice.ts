import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";

export function createUiSlice(set: SliceSet, _get: SliceGet) {
  return {
    setOpenModal: (name: string | null) =>
      set((draft) => {
        draft.openModal = name;
      }),

    setLlmConfig: (config: Partial<AppState["llmConfig"]>) =>
      set((draft) => {
        Object.assign(draft.llmConfig, config);
      }),

    toggleSettings: () =>
      set((draft) => {
        draft.settingsOpen = !draft.settingsOpen;
      }),

    openSettings: (tab: "llm" | "deps" | "api" | "env" = "llm") =>
      set((draft) => {
        draft.settingsOpen = true;
        draft.settingsTab = tab;
      }),

    setSettingsTab: (tab: "llm" | "deps" | "api" | "env") =>
      set((draft) => {
        draft.settingsTab = tab;
      }),
  } as Pick<AppState, 'setOpenModal' | 'setLlmConfig' | 'toggleSettings' | 'openSettings' | 'setSettingsTab'>;
}
