/**
 * W5: loop fail → retry available; success + autoAdopt → adoptDoePlanToWorkbench.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const awaitTaskStream = vi.fn();
const loopIterate = vi.fn(async () => ({ task_id: "loop-new" }));
const createWorkbenchCampaign = vi.fn(async () => ({
  campaign_id: 42,
  rows: [],
  name: "wb",
  strategy: "test",
}));

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    awaitTaskStream,
    api: {
      ...actual.api,
      loopIterate,
      createWorkbenchCampaign,
      getEnvFlags: vi.fn(async () => ({ flags: [] })),
      validateFormulations: vi.fn(async ({ formulations }: { formulations: unknown }) => ({
        formulations,
        warnings: [],
      })),
    },
  };
});

vi.mock("../formulationEnrich", () => ({
  applyEnrichedLeaderboard: vi.fn(
    async (
      set: (fn: (d: Record<string, unknown>) => void) => void,
      _get: unknown,
      _forms: unknown,
      applyDraft: (d: Record<string, unknown>) => void,
    ) => {
      set((draft) => {
        applyDraft(draft);
      });
    },
  ),
  enrichFormulationsViaValidate: vi.fn(async (x: unknown) => x),
}));

// Import store AFTER mocks so workflowSlice binds mocked awaitTaskStream / enrich.
const { useStore } = await import("../index");

const nextDoe = {
  design: "lhs" as const,
  factors: [],
  runs: [{ run_id: 1, coded: {}, natural: { a: 1 } }],
  notes: "n",
  plan_id: "plan-w5",
  domain: "anticorrosion_coating" as const,
};

function okReport(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      optimization: { top_formulations: [], history: [] },
      next_doe: nextDoe,
      rmse_by_metric: { salt_spray_hours: 12 },
      model_info: [],
      engine: "legacy",
      total_records: 1,
      converged: false,
      ...overrides,
    },
  };
}

describe("workflowSlice loop retry / auto-adopt (W5)", () => {
  beforeEach(() => {
    awaitTaskStream.mockReset();
    loopIterate.mockClear();
    createWorkbenchCampaign.mockClear();
    useStore.setState({
      busy: "idle",
      error: null,
      loopRetryAvailable: false,
      lastLoopTaskId: null,
      autoAdoptNextDoeOnLoop: false,
      autoLoopOnSync: false,
      doePlan: null,
      loopReport: null,
      rmseHistory: [],
      leaderboard: [],
      workbenchAdoptedPlanId: null,
      models: [],
      optimizationHistory: [],
      adaptiveDoe: null,
      campaignState: null,
      lastAlEngine: null,
      activeProjectId: "p-w5",
    } as never);
  });

  it("marks loopRetryAvailable on follow failure", async () => {
    awaitTaskStream.mockRejectedValueOnce(new Error("broker down"));
    await useStore.getState().followLoopTask("loop-fail-1");
    const s = useStore.getState();
    expect(s.loopRetryAvailable).toBe(true);
    expect(s.lastLoopTaskId).toBe("loop-fail-1");
    expect(s.error).toMatch(/broker down/);
    expect(s.busy).toBe("idle");
  });

  it("retryLoop clears failure flag then calls runLoop", async () => {
    const runLoop = vi.fn(async () => undefined);
    useStore.setState({
      loopRetryAvailable: true,
      error: "prev",
      lastLoopTaskId: "old",
      runLoop,
    } as never);
    await useStore.getState().retryLoop();
    expect(useStore.getState().loopRetryAvailable).toBe(false);
    expect(useStore.getState().error).toBeNull();
    expect(runLoop).toHaveBeenCalledTimes(1);
  });

  it("does not auto-adopt when flag is off", async () => {
    awaitTaskStream.mockResolvedValueOnce(okReport());
    useStore.setState({ autoAdoptNextDoeOnLoop: false } as never);
    await useStore.getState().followLoopTask("loop-ok");
    expect(createWorkbenchCampaign).not.toHaveBeenCalled();
    expect(useStore.getState().loopRetryAvailable).toBe(false);
  });

  it("auto-adopts next_doe when flag is on", async () => {
    awaitTaskStream.mockResolvedValueOnce(okReport());
    useStore.setState({ autoAdoptNextDoeOnLoop: true } as never);
    await useStore.getState().followLoopTask("loop-ok-adopt");
    expect(createWorkbenchCampaign).toHaveBeenCalled();
    const calls = createWorkbenchCampaign.mock.calls as unknown as unknown[][];
    const first = calls[0]?.[0] as { plan_id?: string } | undefined;
    expect(first?.plan_id).toBe("plan-w5");
  });
});
