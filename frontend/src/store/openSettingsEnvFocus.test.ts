import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "./index";

describe("openSettings env focus", () => {
  beforeEach(() => {
    useStore.setState({
      settingsOpen: false,
      settingsTab: "llm",
      settingsEnvFocusAttr: null,
    } as never);
  });

  it("openSettings env with focusEnvAttr stores the attr", () => {
    useStore.getState().openSettings("env", {
      focusEnvAttr: "wiki_dossier_report_enabled",
    });
    const s = useStore.getState();
    expect(s.settingsOpen).toBe(true);
    expect(s.settingsTab).toBe("env");
    expect(s.settingsEnvFocusAttr).toBe("wiki_dossier_report_enabled");
  });

  it("openSettings non-env clears focus", () => {
    useStore.setState({ settingsEnvFocusAttr: "wiki_enabled" } as never);
    useStore.getState().openSettings("deps");
    expect(useStore.getState().settingsEnvFocusAttr).toBeNull();
  });

  it("setSettingsTab away from env clears focus", () => {
    useStore.setState({
      settingsOpen: true,
      settingsTab: "env",
      settingsEnvFocusAttr: "wiki_project_dossier_enabled",
    } as never);
    useStore.getState().setSettingsTab("llm");
    expect(useStore.getState().settingsEnvFocusAttr).toBeNull();
  });

  it("clearSettingsEnvFocus clears attr", () => {
    useStore.setState({ settingsEnvFocusAttr: "wiki_enabled" } as never);
    useStore.getState().clearSettingsEnvFocus();
    expect(useStore.getState().settingsEnvFocusAttr).toBeNull();
  });
});
