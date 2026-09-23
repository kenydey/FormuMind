import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import KgFeedbackStatsStrip from "./KgFeedbackStatsStrip";

const kgFeedbackStats = vi.fn();

vi.mock("../api", () => ({
  api: {
    kgFeedbackStats: (...args: unknown[]) => kgFeedbackStats(...args),
  },
}));

describe("KgFeedbackStatsStrip", () => {
  beforeEach(() => {
    kgFeedbackStats.mockReset();
  });

  it("renders material-level counts when present", async () => {
    kgFeedbackStats.mockResolvedValue({
      measured_total: 4,
      measured_performance: 4,
      measured_material: 3,
      measured_domain: 1,
      by_campaign: {},
    });
    render(<KgFeedbackStatsStrip />);
    await waitFor(() => {
      expect(screen.getByTestId("kg-feedback-stats-strip")).toBeTruthy();
    });
    const el = screen.getByTestId("kg-feedback-stats-strip");
    expect(el.textContent).toContain("材料级 3");
    expect(el.getAttribute("data-measured-material")).toBe("3");
  });

  it("hides compact strip when total is zero", async () => {
    kgFeedbackStats.mockResolvedValue({
      measured_total: 0,
      measured_performance: 0,
      measured_material: 0,
      measured_domain: 0,
      by_campaign: {},
    });
    const { container } = render(<KgFeedbackStatsStrip compact />);
    await waitFor(() => {
      expect(kgFeedbackStats).toHaveBeenCalled();
    });
    expect(container.querySelector("[data-testid=kg-feedback-stats-strip]")).toBeNull();
  });

  it("shows empty guidance on workbench (non-compact) when zero", async () => {
    kgFeedbackStats.mockResolvedValue({
      measured_total: 0,
      measured_performance: 0,
      by_campaign: {},
    });
    render(<KgFeedbackStatsStrip />);
    await waitFor(() => {
      expect(screen.getByTestId("kg-feedback-stats-strip").textContent).toContain(
        "0 条",
      );
    });
  });
});
