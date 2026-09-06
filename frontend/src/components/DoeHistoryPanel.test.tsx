import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import DoeHistoryPanel from "./DoeHistoryPanel";

vi.mock("../store", () => ({
  useStore: (sel: (s: { workbenchCampaignId: number | null }) => unknown) =>
    sel({ workbenchCampaignId: 11 }),
}));

describe("DoeHistoryPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads history when expanded", async () => {
    const spy = vi.spyOn(api, "listDoeHistory").mockResolvedValue({
      items: [
        {
          plan_id: "p1",
          design: "lhs",
          round: 2,
          runs: [{}, {}],
          notes: "batch A",
          created_at: "2026-09-06T12:00:00",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    });
    render(<DoeHistoryPanel />);
    expect(screen.getByTestId("doe-history-panel")).toBeTruthy();
    await userEvent.click(screen.getByTestId("doe-history-toggle"));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0]).toMatchObject({ campaignId: 11 });
    expect(await screen.findByTestId("doe-history-list")).toBeTruthy();
    expect(screen.getByText("lhs")).toBeTruthy();
    expect(screen.getByText(/batch A/)).toBeTruthy();
  });
});
