/**
 * W5 LoopModal: retry banner + auto-adopt checkbox.
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import LoopModal from "./LoopModal";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getEnvFlags: vi.fn(async () => ({
        flags: [
          {
            attr: "auto_adopt_next_doe_on_loop",
            env_key: "FORMUMIND_AUTO_ADOPT_NEXT_DOE_ON_LOOP",
            label: "闭环后自动采纳下一轮 DOE",
            description: "d",
            category: "data",
            category_label: "数据",
            hint: "",
            value: false,
            default: false,
          },
        ],
      })),
      getDoeCycleStatus: vi.fn(async () => ({ isPaused: false })),
    },
  };
});

describe("LoopModal W5 retry / auto-adopt", () => {
  beforeEach(() => {
    useStore.setState({
      busy: "idle",
      error: "broker down",
      loopRetryAvailable: true,
      autoAdoptNextDoeOnLoop: false,
      autoLoopOnSync: false,
      autoLoopMaxRounds: 5,
      autoLoopRound: 0,
      loopReport: null,
      doePlan: null,
      workbenchCampaignId: 1,
      workbenchAdoptedPlanId: null,
      optimizeEngine: "auto",
      loopDoeEngine: "auto",
      task: null,
      envFlagsRevision: 0,
      retryLoop: vi.fn(async () => undefined),
      setAutoAdoptNextDoeOnLoop: (v: boolean) =>
        useStore.setState({ autoAdoptNextDoeOnLoop: v } as never),
    } as never);
  });

  it("shows retry banner and fires retryLoop", async () => {
    const retryLoop = vi.fn(async () => undefined);
    useStore.setState({ retryLoop } as never);
    render(<LoopModal />);
    expect(await screen.findByTestId("loop-retry-banner")).toBeTruthy();
    fireEvent.click(screen.getByTestId("loop-retry-btn"));
    expect(retryLoop).toHaveBeenCalled();
  });

  it("toggles auto-adopt checkbox", async () => {
    render(<LoopModal />);
    const box = (await screen.findByTestId("loop-auto-adopt")).querySelector("input")!;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(useStore.getState().autoAdoptNextDoeOnLoop).toBe(true);
  });
});
