/**
 * Workspace save/load helpers.
 *
 * `workspaceSlice` (save) and `applyPatchToDraft` (load) are two
 * hand-maintained lists of the same fields. Add a field to one and forget the
 * other and the app still compiles, still saves, and silently drops the value
 * on reload — the round-trip test below is the only thing that catches it.
 */
import { describe, expect, it } from "vitest";
import type { AppState } from "./types";
import {
  applyPatchToDraft,
  defaultRequirement,
  objectiveTargetFromRequirement,
  workspaceSlice,
} from "./helpers";

/** A state where every persisted field carries a distinctive value. */
function populatedState(): AppState {
  return {
    searchQuery: "epoxy primer",
    sourceTypes: ["patents", "literature"],
    sources: [{ source: "Patent", identifier: "US1", title: "T", snippet: "s", relevance: 0.9 }],
    selectedSources: ["US1"],
    chatHistory: [{ role: "user", content: "hi" }],
    deepReport: { topic: "t", report_markdown: "md" },
    requirement: { ...defaultRequirement, salt_spray_hours: 1000 },
    activeConstraints: ["VOC 上限"],
    research: { requirement_headline: "h", evidence: [] },
    leaderboard: [{ name: "F1" }],
    doePlan: { design: "lhs", runs: [] },
    adaptiveDoe: { strategy_label: "explore" },
    measured: { 1: { salt_spray_hours: 700 } },
    models: [{ metric: "salt_spray_hours" }],
    modelHistory: [{ at: 1 }],
    trainMessage: "trained",
    campaignState: "state-blob",
    workbenchCampaignId: 7,
    workbenchAdoptedPlanId: "plan-1",
    workbenchObjectivesSnapshot: [{ metric: "salt_spray_hours" }],
    optimizationHistory: [0.1, 0.4],
    loopReport: { domain: "anticorrosion_coating" },
    rmseHistory: [{ salt_spray_hours: 12 }],
    processOptResult: { domain: "anticorrosion_coating" },
    doeEngine: "pydoe",
    alEngine: "baybe",
    optimizeEngine: "botorch",
    loopDoeEngine: "lhs",
    recommendSourceTypes: ["patents"],
    lastAlEngine: "baybe",
    autoLoopOnSync: true,
  } as unknown as AppState;
}

describe("workspace round trip", () => {
  it("restores every field it saves", () => {
    // The real assertion: save -> load -> save must be a fixed point. If a
    // field exists in workspaceSlice but is missing from applyPatchToDraft,
    // the reloaded value differs and this fails.
    const saved = workspaceSlice(populatedState());

    const draft = {} as AppState;
    applyPatchToDraft(draft, saved);

    expect(workspaceSlice(draft)).toEqual(saved);
  });

  it("names every saved field in the reload path", () => {
    const saved = workspaceSlice(populatedState());
    const draft = {} as AppState;
    applyPatchToDraft(draft, saved);

    const dropped = Object.keys(saved).filter(
      (key) => (draft as unknown as Record<string, unknown>)[key] === undefined
    );
    expect(dropped).toEqual([]);
  });

  it("leaves fields absent from the patch untouched", () => {
    // A partial patch must not blank out unrelated state.
    const draft = { searchQuery: "keep me", trainMessage: "keep too" } as AppState;
    applyPatchToDraft(draft, { trainMessage: "replaced" });
    expect(draft.searchQuery).toBe("keep me");
    expect(draft.trainMessage).toBe("replaced");
  });

  it("applies falsy values rather than skipping them", () => {
    // `!== undefined` and not truthiness — clearing a query to "" or turning
    // autoLoopOnSync off must persist.
    const draft = { searchQuery: "old", autoLoopOnSync: true } as AppState;
    applyPatchToDraft(draft, { searchQuery: "", autoLoopOnSync: false });
    expect(draft.searchQuery).toBe("");
    expect(draft.autoLoopOnSync).toBe(false);
  });

  it("is a no-op for an empty patch", () => {
    const draft = { searchQuery: "unchanged" } as AppState;
    applyPatchToDraft(draft, {});
    expect(draft.searchQuery).toBe("unchanged");
  });
});

describe("objectiveTargetFromRequirement", () => {
  const req = {
    ...defaultRequirement,
    salt_spray_hours: 1000,
    film_weight_gsm: 70,
    cleaning_efficiency: 95,
  };

  it("maps the metrics that have a requirement field", () => {
    expect(objectiveTargetFromRequirement(req, "salt_spray_hours")).toBe(1000);
    expect(objectiveTargetFromRequirement(req, "cleaning_efficiency")).toBe(95);
  });

  it("routes both film-weight aliases to the same field", () => {
    // The backend names it coating_weight_gsm for surface treatment and
    // film_weight_gsm for coatings; both read the one requirement input.
    expect(objectiveTargetFromRequirement(req, "film_weight_gsm")).toBe(70);
    expect(objectiveTargetFromRequirement(req, "coating_weight_gsm")).toBe(70);
  });

  it("returns null for a metric with no requirement input", () => {
    // Cost has no target field, so the UI must show no target rather than 0.
    expect(objectiveTargetFromRequirement(req, "cost_cny_per_kg")).toBeNull();
    expect(objectiveTargetFromRequirement(req, "anything_custom")).toBeNull();
  });
});
