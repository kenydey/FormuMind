/**
 * exportDoe used ``window.open(url)``: a navigation cannot carry the bearer token, so with
 * API auth on (the default for public deployments) the DOE CSV/XLSX export answered 401 in
 * a blank tab. It must go through the authenticated download helper and report failures.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const calls: Array<[string, string | undefined]> = [];
let behaviour: () => Promise<string> = async () => "x";

// Plain function behind the mock: vitest 4 flags a spied async rejection as unhandled even
// when the code under test awaits it inside try/catch.
vi.mock("../../utils/download", () => ({
  downloadWithAuth: (url: string, name?: string) => {
    calls.push([url, name]);
    return behaviour();
  },
}));

const { useStore } = await import("../index");

const plan = {
  design: "lhs" as const,
  factors: [],
  runs: [],
  notes: "",
  plan_id: "plan-7",
  domain: "anticorrosion_coating" as const,
};

describe("workflowSlice.exportDoe", () => {
  let openSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    calls.length = 0;
    behaviour = async () => "x";
    openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    useStore.setState({ doePlan: plan, error: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("downloads through the authenticated helper, never window.open", async () => {
    await useStore.getState().exportDoe("xlsx");
    expect(calls).toEqual([["/api/doe/plan-7/export?format=xlsx", "doe_plan-7.xlsx"]]);
    expect(openSpy).not.toHaveBeenCalled();
    expect(useStore.getState().error).toBeNull();
  });

  it("surfaces a failed export (e.g. 401) in the store error", async () => {
    behaviour = async () => {
      throw new Error("Unauthorized");
    };
    await useStore.getState().exportDoe("csv");
    expect(useStore.getState().error).toMatch(/Unauthorized/);
  });

  it("asks for a plan first and does not call the backend", async () => {
    useStore.setState({ doePlan: null });
    await useStore.getState().exportDoe("csv");
    expect(calls).toEqual([]);
    expect(useStore.getState().error).toContain("请先生成 DOE 计划");
  });
});
