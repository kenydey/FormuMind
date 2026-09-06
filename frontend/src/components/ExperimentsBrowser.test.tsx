import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import type { AppState } from "../store/types";
import ExperimentsBrowser from "./ExperimentsBrowser";

describe("ExperimentsBrowser", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useStore.setState({
      activeProjectId: "proj-1",
      requirement: { ...(useStore.getState().requirement as object), domain: "coating" },
    } as unknown as AppState);
  });

  it("lists experiments for the active project", async () => {
    vi.spyOn(api, "listExperiments").mockResolvedValue([
      {
        id: 7,
        domain: "coating",
        label: "SS-1000",
        source: "workbench",
        project_id: "proj-1",
        measured: { salt_spray_hours: 1000 },
        measurement_count: 1,
        created_at: "2026-09-01T10:00:00",
      },
    ]);
    render(<ExperimentsBrowser />);
    expect(await screen.findByTestId("experiments-browser")).toBeTruthy();
    await waitFor(() => expect(api.listExperiments).toHaveBeenCalled());
    expect(screen.getByText(/SS-1000/)).toBeTruthy();
    expect(screen.getByText(/#7/)).toBeTruthy();
  });
});
