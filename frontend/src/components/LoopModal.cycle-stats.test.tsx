/**
 * Wave 3-2: LoopModal "循环统计" section — per-project cycle observability.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import LoopModal from "./LoopModal";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getEnvFlags: vi.fn(async () => ({ flags: [] })),
      getDoeCycleStatus: vi.fn(async () => ({ isPaused: false })),
      getDoeCycleRuns: vi.fn(async () => ({
        items: [],
        summary: {
          cycle_count: 3,
          total_experiments: 15,
          measured_count: 8,
          last_engine: "baybe",
          last_status: "success",
        },
      })),
    },
  };
});

const getDoeCycleRuns = () => (api as unknown as { getDoeCycleRuns: ReturnType<typeof vi.fn> }).getDoeCycleRuns;

describe("LoopModal cycle stats (Wave 3-2)", () => {
  beforeEach(() => {
    getDoeCycleRuns().mockClear();
    useStore.setState({
      busy: "idle",
      error: null,
      loopRetryAvailable: false,
      loopReport: null,
      rmseHistory: [],
      doePlan: null,
      adaptiveDoe: null,
      workbenchAdoptedPlanId: null,
      optimizeEngine: "auto",
      loopDoeEngine: "auto",
      autoLoopOnSync: false,
      autoLoopMaxRounds: 5,
      autoLoopRound: 0,
      autoAdoptNextDoeOnLoop: false,
      task: null,
      workbenchCampaignId: 1,
      envFlagsRevision: 0,
      activeProjectId: "proj-1",
    } as never);
  });

  it("fetches and shows per-project cycle statistics", async () => {
    render(<LoopModal />);
    const section = await screen.findByTestId("doe-cycle-stats");
    expect(getDoeCycleRuns()).toHaveBeenCalledWith("proj-1");
    expect(section.textContent).toContain("3");
    expect(section.textContent).toContain("15");
    expect(section.textContent).toContain("8");
    expect(section.textContent).toContain("baybe");
    expect(section.textContent).toContain("success");
    expect(section.textContent).toContain("未收敛");
  });

  it("shows converged state when loopReport converged", async () => {
    useStore.setState({ loopReport: { converged: true, model_info: [] } } as never);
    render(<LoopModal />);
    const section = await screen.findByTestId("doe-cycle-stats");
    expect(section.textContent).toContain("已收敛");
  });

  it("hides the section when no active project", async () => {
    useStore.setState({ activeProjectId: null } as never);
    render(<LoopModal />);
    await new Promise((r) => setTimeout(r, 50));
    expect(getDoeCycleRuns()).not.toHaveBeenCalled();
    expect(screen.queryByTestId("doe-cycle-stats")).toBeNull();
  });

  it("fail-open: hides the section when the API errors", async () => {
    getDoeCycleRuns().mockRejectedValueOnce(new Error("down"));
    render(<LoopModal />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("doe-cycle-stats")).toBeNull();
  });
});
