/**
 * U-1: DOE 状态机找消费方 —— adopt 写 active，runNextRound 写旧 plan completed。
 * doePlanTransition fail-open（mock 内部吞错），采纳流程不受影响。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const createWorkbenchCampaign = vi.fn(async () => ({
  campaign_id: 42,
  rows: [],
  name: "wb",
  strategy: "test",
}));
const doePlanTransition = vi.fn(
  async (): Promise<{ plan_id: string; status: string } | null> => ({
    plan_id: "x",
    status: "active",
  })
);
const activeDoe = vi.fn();

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      createWorkbenchCampaign,
      doePlanTransition,
      activeDoe,
      refreshWorkbenchStats: undefined,
      getEnvFlags: vi.fn(async () => ({ flags: [] })),
      validateFormulations: vi.fn(async ({ formulations }: { formulations: unknown }) => ({
        formulations,
        warnings: [],
      })),
    },
  };
});

vi.mock("../formulationEnrich", () => ({
  applyEnrichedLeaderboard: vi.fn(async () => {}),
  enrichFormulationsViaValidate: vi.fn(async (x: unknown) => x),
}));

// Import store AFTER mocks.
const { useStore } = await import("../index");

const planA = {
  design: "lhs" as const,
  factors: [],
  runs: [],
  notes: "a",
  plan_id: "plan-A",
  domain: "anticorrosion_coating" as const,
};
const planB = { ...planA, plan_id: "plan-B", notes: "b" };

describe("workflowSlice U-1 doe status consumers", () => {
  beforeEach(() => {
    createWorkbenchCampaign.mockClear();
    doePlanTransition.mockClear();
    activeDoe.mockReset();
    useStore.setState({
      doePlan: planA,
      requirement: { domain: "anticorrosion_coating" } as never,
      busy: "idle",
      error: null,
    });
  });

  it("adoptDoePlanToWorkbench 成功后写 active（fail-open）", async () => {
    const id = await useStore.getState().adoptDoePlanToWorkbench(planA);
    expect(id).toBe(42);
    expect(doePlanTransition).toHaveBeenCalledWith("plan-A", "activate");
  });

  it("doePlanTransition 返回 null（真实实现内部 .catch 吞错）不阻断采纳", async () => {
    doePlanTransition.mockResolvedValueOnce(null);
    const id = await useStore.getState().adoptDoePlanToWorkbench(planA);
    expect(id).toBe(42);
  });

  it("无 plan_id 时不调 transition", async () => {
    const noId = { ...planA, plan_id: undefined };
    await useStore.getState().adoptDoePlanToWorkbench(noId as never);
    expect(doePlanTransition).not.toHaveBeenCalled();
  });

  it("runNextRoundDoe：新一轮采纳后旧 plan → completed", async () => {
    activeDoe.mockResolvedValue({
      campaign_state: "s1",
      engine: "legacy",
      plan: planB,
    });
    // 绕过 adopt 分支：让 doePlan.plan_id === workbenchAdoptedPlanId
    useStore.setState({ workbenchAdoptedPlanId: "plan-A" });
    await useStore.getState().runNextRoundDoe();
    expect(activeDoe).toHaveBeenCalled();
    // adopt 新 plan → activate(plan-B)；旧 plan-A → complete
    const calls = doePlanTransition.mock.calls.map((c) => c.join("/"));
    expect(calls).toContain("plan-B/activate");
    expect(calls).toContain("plan-A/complete");
  });

  it("P2: transition 抛 422 时错误可见、采纳不受影响", async () => {
    doePlanTransition.mockRejectedValueOnce(new Error("request failed with 422"));
    const id = await useStore.getState().adoptDoePlanToWorkbench(planA);
    expect(id).toBe(42);
    // 微任务 flush：catch 回调是异步的
    await new Promise((r) => setTimeout(r, 0));
    expect(useStore.getState().error).toMatch(/状态同步失败/);
  });
});
