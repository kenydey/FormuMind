import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import type { AppState } from "../store/types";
import WorkbenchModal from "./WorkbenchModal";

describe("WorkbenchModal tabs", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useStore.setState({
      activeProjectId: "proj-1",
      doePlan: null,
      workbenchCampaignId: null,
      workbenchStats: null,
      busy: "idle",
      requirement: { ...(useStore.getState().requirement as object), domain: "coating" },
      ensureWorkbenchCampaign: vi.fn(async () => null),
      selectWorkbenchCampaign: vi.fn(async () => undefined),
      refreshWorkbenchStats: vi.fn(async () => undefined),
      submitResults: vi.fn(async () => undefined),
      setOpenModal: vi.fn(),
    } as unknown as AppState);
    vi.spyOn(api, "listWorkbenchCampaigns").mockResolvedValue([]);
    vi.spyOn(api, "listExperiments").mockResolvedValue([]);
  });

  it("shows ledger/library tabs and can open the library tab without a campaign", async () => {
    render(<WorkbenchModal />);
    expect(await screen.findByTestId("workbench-tabs")).toBeTruthy();
    expect(screen.getByTestId("workbench-tab-ledger")).toBeTruthy();
    await userEvent.click(screen.getByTestId("workbench-tab-library"));
    expect(await screen.findByTestId("experiments-browser")).toBeTruthy();
  });

  it("opens on library tab when initialTab=library", async () => {
    render(<WorkbenchModal initialTab="library" />);
    expect(await screen.findByTestId("experiments-browser")).toBeTruthy();
  });

  it("jumps from a search hit to the ledger tab and selects the campaign", async () => {
    const selectSpy = vi.fn(async () => {
      useStore.setState({ workbenchCampaignId: 3 } as Partial<AppState> as AppState);
    });
    useStore.setState({
      selectWorkbenchCampaign: selectSpy,
      workbenchCampaignId: 1,
      doePlan: { plan_id: "p1", design: "lhs", runs: [] } as never,
    } as unknown as AppState);
    vi.spyOn(api, "listWorkbenchCampaigns").mockResolvedValue([
      {
        id: 3,
        name: "DOE-A",
        status: "active",
        strategy: "lhs",
        row_count: 2,
        project_id: "proj-1",
      },
    ]);
    vi.spyOn(api, "getWorkbenchCampaign").mockResolvedValue({
      campaign_id: 3,
      name: "DOE-A",
      status: "active",
      strategy: "lhs",
      rows: [
        {
          id: 12,
          campaign_id: 3,
          status: "Done",
          planned_params: {},
          actual_params: {},
          measurements: {},
        },
      ],
    } as never);
    vi.spyOn(api, "searchExperiments").mockResolvedValue([
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

    render(<WorkbenchModal initialTab="library" />);
    expect(await screen.findByTestId("experiments-cross-search")).toBeTruthy();
    await userEvent.type(screen.getByTestId("experiments-search-input"), "urgent");
    await userEvent.click(screen.getByTestId("experiments-search-btn"));
    const hit = await screen.findByTestId("experiments-search-hit");
    await userEvent.click(hit);
    await waitFor(() => expect(selectSpy).toHaveBeenCalledWith(3));
    expect(screen.getByTestId("workbench-tab-ledger").getAttribute("aria-selected")).toBe("true");
  });
});
