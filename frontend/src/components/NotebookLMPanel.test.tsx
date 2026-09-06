import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type NotebookLMStatus } from "../api";
import { useStore } from "../store";
import type { AppState } from "../store/types";
import NotebookLMPanel from "./NotebookLMPanel";

const STATUS: NotebookLMStatus = {
  available: false,
  reason: "library_missing",
  hint: "安装 notebooklm-py",
  lib_installed: false,
  enabled: false,
  notebook_id_set: false,
  notebook_id: null,
  session_present: false,
  can_launch_browser: true,
};

describe("NotebookLMPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "notebooklmStatus").mockResolvedValue(STATUS);
    useStore.setState({
      openSettings: vi.fn(),
    } as Partial<AppState> as AppState);
  });

  it("loads auth status and offers deps shortcut when lib missing", async () => {
    render(<NotebookLMPanel />);
    expect(await screen.findByTestId("notebooklm-panel")).toBeTruthy();
    expect(screen.getAllByText(/notebooklm-py/).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /依赖管理/ }));
    await waitFor(() => {
      expect(useStore.getState().openSettings).toHaveBeenCalledWith("deps");
    });
  });
});
