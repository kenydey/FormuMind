/**
 * LoopModal pause indicator: a pause says when it ends - or that it lasts until resumed - and a pause that ran
 * out by itself is reported instead of the loop quietly starting again.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import LoopModal from "./LoopModal";

type Status = {
  isPaused: boolean;
  lastUpdated: string | null;
  campaignId: number;
  pausedUntil?: string | null;
  lapsedAt?: string | null;
};

const getDoeCycleStatus = vi.fn<() => Promise<Status>>();
const postDoeCyclePause = vi.fn(async () => ({ status: "success", message: "ok" }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getEnvFlags: vi.fn(async () => ({ flags: [] })),
      getDoeCycleRuns: vi.fn(async () => ({ items: [], summary: null })),
      getDoeCycleStatus: (...args: unknown[]) => (getDoeCycleStatus as (...a: unknown[]) => Promise<Status>)(...args),
      postDoeCyclePause: (...args: unknown[]) => (postDoeCyclePause as (...a: unknown[]) => Promise<unknown>)(...args),
    },
  };
});

const STAMP = /\d{1,2}[/-]\d{1,2}.*\d{1,2}:\d{2}/;

describe("LoopModal pause indicator", () => {
  beforeEach(() => {
    getDoeCycleStatus.mockReset();
    postDoeCyclePause.mockClear();
    useStore.setState({
      busy: "idle",
      error: null,
      loopRetryAvailable: false,
      loopReport: null,
      doePlan: null,
      workbenchCampaignId: 7,
      workbenchAdoptedPlanId: null,
      optimizeEngine: "auto",
      loopDoeEngine: "auto",
      autoLoopOnSync: false,
      autoLoopMaxRounds: 5,
      autoLoopRound: 0,
      autoAdoptNextDoeOnLoop: false,
      task: null,
      envFlagsRevision: 0,
      activeProjectId: null,
    } as never);
  });

  it("says when a timed pause ends", async () => {
    getDoeCycleStatus.mockResolvedValue({
      isPaused: true, lastUpdated: "2026-10-02T09:00:00Z", campaignId: 7, pausedUntil: "2026-10-03T09:00:00Z",
    });
    render(<LoopModal />);
    const note = await screen.findByTestId("doe-cycle-paused-until");
    expect(note.textContent).toMatch(/自动恢复/);
    expect(note.textContent).toMatch(STAMP);
    expect(screen.getByTestId("doe-cycle-status").textContent).toMatch(/已暂停/);
  });

  it("says a pause without an end lasts until it is resumed", async () => {
    getDoeCycleStatus.mockResolvedValue({
      isPaused: true, lastUpdated: "2026-10-02T09:00:00Z", campaignId: 7, pausedUntil: null,
    });
    render(<LoopModal />);
    expect((await screen.findByTestId("doe-cycle-paused-until")).textContent).toBe("恢复前一直暂停");
  });

  it("reports a pause that lapsed instead of silently resuming", async () => {
    getDoeCycleStatus.mockResolvedValue({
      isPaused: false, lastUpdated: null, campaignId: 7, pausedUntil: null, lapsedAt: "2026-10-03T09:00:00Z",
    });
    render(<LoopModal />);
    const note = await screen.findByTestId("doe-cycle-lapsed");
    expect(note.textContent).toMatch(/到期，闭环已自动恢复/);
    expect(note.textContent).toMatch(STAMP);
    expect(screen.getByTestId("doe-cycle-status").textContent).toMatch(/运行中/);
    expect(screen.queryByTestId("doe-cycle-paused-until")).toBeNull();
  });

  it("shows neither for a loop that was never paused (and tolerates the old payload)", async () => {
    getDoeCycleStatus.mockResolvedValue({ isPaused: false, lastUpdated: null, campaignId: 7 });
    render(<LoopModal />);
    await waitFor(() => expect(getDoeCycleStatus).toHaveBeenCalled());
    expect(screen.queryByTestId("doe-cycle-paused-until")).toBeNull();
    expect(screen.queryByTestId("doe-cycle-lapsed")).toBeNull();
  });

  it("toggling re-reads the state, so the new end time shows at once", async () => {
    getDoeCycleStatus.mockResolvedValue({ isPaused: false, lastUpdated: null, campaignId: 7 });
    render(<LoopModal />);
    await waitFor(() => expect(getDoeCycleStatus).toHaveBeenCalledTimes(1));
    getDoeCycleStatus.mockResolvedValue({
      isPaused: true, lastUpdated: "2026-10-02T09:00:00Z", campaignId: 7, pausedUntil: "2026-10-03T09:00:00Z",
    });
    fireEvent.click(screen.getByTestId("doe-cycle-pause"));
    expect((await screen.findByTestId("doe-cycle-paused-until")).textContent).toMatch(/自动恢复/);
    expect(postDoeCyclePause).toHaveBeenCalledWith(7, true);
  });

  it("shows nothing about a pause when no campaign is bound", async () => {
    useStore.setState({ workbenchCampaignId: null } as never);
    render(<LoopModal />);
    expect(screen.getByTestId("doe-cycle-status").textContent).toMatch(/未绑定台账/);
    expect(screen.queryByTestId("doe-cycle-paused-until")).toBeNull();
    expect(getDoeCycleStatus).not.toHaveBeenCalled();
  });
});
