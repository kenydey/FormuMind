/**
 * projectWorkspace sanitize — guards PUT /api/projects against 422 from
 * stray null/string entries in object arrays (legacy chat / malformed sources).
 */
import { describe, expect, it } from "vitest";
import { buildWorkspacePayload, type StoreWorkspaceSlice } from "./projectWorkspace";
import { defaultRequirement } from "./store/helpers";

function baseSlice(over: Partial<StoreWorkspaceSlice> = {}): StoreWorkspaceSlice {
  return {
    searchQuery: "",
    sourceTypes: ["literature"],
    sources: [],
    selectedSources: [],
    chatHistory: [],
    deepReport: null,
    requirement: { ...defaultRequirement },
    activeConstraints: [],
    research: null,
    leaderboard: [],
    doePlan: null,
    adaptiveDoe: null,
    measured: {},
    models: [],
    modelHistory: [],
    trainMessage: "",
    campaignState: null,
    workbenchCampaignId: null,
    workbenchAdoptedPlanId: null,
    workbenchObjectivesSnapshot: null,
    optimizationHistory: [],
    loopReport: null,
    rmseHistory: [],
    doeEngine: "auto",
    alEngine: "auto",
    optimizeEngine: "auto",
    loopDoeEngine: "auto",
    recommendSourceTypes: ["literature"],
    lastAlEngine: null,
    autoLoopOnSync: false,
    autoLoopMaxRounds: 3,
    autoLoopRound: 0,
    ...over,
  };
}

describe("buildWorkspacePayload sanitize", () => {
  it("drops null/string entries from sources and leaderboard", () => {
    const payload = buildWorkspacePayload(
      baseSlice({
        sources: [
          { title: "ok", url: "https://x", snippet: "" } as never,
          null as never,
          "legacy-string" as never,
        ],
        leaderboard: [
          { name: "A", domain: "anticorrosion_coating", ingredients: [] } as never,
          undefined as never,
          42 as never,
        ],
        modelHistory: [[{ name: "m" } as never, null as never]],
      })
    );

    expect(payload.sources).toHaveLength(1);
    expect(payload.sources[0]).toMatchObject({ title: "ok" });
    expect(payload.leaderboard).toHaveLength(1);
    expect(payload.leaderboard[0]).toMatchObject({ name: "A" });
    expect(payload.model_history[0]).toHaveLength(1);
  });

  it("coerces non-arrays to empty arrays and non-objects to null", () => {
    const payload = buildWorkspacePayload(
      baseSlice({
        sources: null as never,
        chatHistory: undefined as never,
        leaderboard: "nope" as never,
        deepReport: "x" as never,
        models: null as never,
      })
    );
    expect(payload.sources).toEqual([]);
    expect(payload.leaderboard).toEqual([]);
    expect(payload.deep_report).toBeNull();
  });
});
