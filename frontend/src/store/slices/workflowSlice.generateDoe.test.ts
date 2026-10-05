/**
 * generateDoe hands the CCD star-point choice to the API for the central composite design only - every other design
 * (and the AI-selected one, which has its own endpoint) never sees `ccd_alpha`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const doe = vi.fn();
const activeDoe = vi.fn();

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      doe,
      activeDoe,
      createWorkbenchCampaign: vi.fn(async () => ({ campaign_id: 7, rows: [], name: "wb", strategy: "test" })),
      doePlanTransition: vi.fn(async () => null),
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

// Import the store AFTER the mocks.
const { useStore } = await import("../index");

const plan = {
  design: "ccd" as const,
  factors: [],
  runs: [],
  notes: "n",
  plan_id: "plan-1",
  domain: "anticorrosion_coating" as const,
};

describe("generateDoe ccd_alpha", () => {
  beforeEach(() => {
    doe.mockReset();
    doe.mockResolvedValue(plan);
    activeDoe.mockReset();
    useStore.setState({
      doePlan: null,
      doeEngine: "native",
      requirement: { domain: "anticorrosion_coating" } as never,
      busy: "idle",
      error: null,
    });
  });

  it("passes the rotatable request for ccd", async () => {
    await useStore.getState().generateDoe("ccd", { ccdAlpha: "rotatable" });
    expect(doe).toHaveBeenCalledWith(expect.anything(), "ccd", "native", { ccdAlpha: "rotatable" });
  });

  it("asks for nothing in particular when the caller did not choose", async () => {
    await useStore.getState().generateDoe("ccd");
    expect(doe).toHaveBeenCalledWith(expect.anything(), "ccd", "native", { ccdAlpha: undefined });
  });

  it("never sends it for another design", async () => {
    await useStore.getState().generateDoe("lhs", { ccdAlpha: "rotatable" });
    expect(doe).toHaveBeenCalledWith(expect.anything(), "lhs", "native", {});
  });

  it("the AI-selected design goes to the active endpoint without it", async () => {
    activeDoe.mockResolvedValue({ campaign_state: null, engine: "legacy", plan });
    await useStore.getState().generateDoe("ai_active", { ccdAlpha: "rotatable" });
    expect(doe).not.toHaveBeenCalled();
    expect(activeDoe).toHaveBeenCalledTimes(1);
  });
});
