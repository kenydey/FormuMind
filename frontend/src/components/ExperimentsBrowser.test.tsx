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

  it("runs cross-campaign search via searchExperiments", async () => {
    vi.spyOn(api, "listExperiments").mockResolvedValue([]);
    const searchSpy = vi.spyOn(api, "searchExperiments").mockResolvedValue([
      {
        row_id: 12,
        campaign_id: 3,
        campaign_name: "DOE-A",
        item_id: "item-12",
        status: "Done",
        planned_params: {},
        measurements: {},
      },
    ]);
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(<ExperimentsBrowser />);
    expect(await screen.findByTestId("experiments-cross-search")).toBeTruthy();
    await user.type(screen.getByTestId("experiments-search-input"), "urgent");
    await user.click(screen.getByTestId("experiments-search-btn"));
    await waitFor(() => expect(searchSpy).toHaveBeenCalledWith("urgent"));
    expect(await screen.findByTestId("experiments-search-hit")).toBeTruthy();
    expect(screen.getByText(/DOE-A/)).toBeTruthy();
    expect(screen.getByText(/row #12/)).toBeTruthy();
  });
});
