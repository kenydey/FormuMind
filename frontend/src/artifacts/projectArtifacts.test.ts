import { describe, expect, it } from "vitest";
import { modalForArtifact, selectProjectArtifacts } from "./projectArtifacts";

const empty = {
  leaderboard: [] as unknown[],
  formulationBusy: false,
  doePlan: null,
  busy: "idle",
  optimizationHistory: [] as unknown[],
  deepReport: null,
  deepResearchBusy: false,
  loopReport: null,
  activeProjectId: null as string | null,
};

describe("selectProjectArtifacts", () => {
  it("returns empty when nothing is ready or running", () => {
    expect(selectProjectArtifacts(empty)).toEqual([]);
  });

  it("includes leaderboard when formulations exist", () => {
    const arts = selectProjectArtifacts({
      ...empty,
      leaderboard: [{ id: "a" }],
    });
    expect(arts).toHaveLength(1);
    expect(arts[0]).toMatchObject({
      id: "leaderboard",
      ready: true,
      busy: false,
      modal: "recommend",
      subtitle: "1 条候选",
    });
  });

  it("includes busy recommend even without results yet", () => {
    const arts = selectProjectArtifacts({ ...empty, formulationBusy: true });
    expect(arts[0]).toMatchObject({ id: "leaderboard", ready: false, busy: true });
  });

  it("includes doe / optimize / deep / loop when present", () => {
    const arts = selectProjectArtifacts({
      ...empty,
      doePlan: { runs: [{}, {}], notes: "ccd" },
      optimizationHistory: [1, 2, 3],
      deepReport: { citations: [{}, {}] },
      loopReport: { ok: true },
    });
    expect(arts.map((a) => a.id)).toEqual([
      "doe_plan",
      "optimization",
      "deep_report",
      "loop_report",
    ]);
    expect(arts.find((a) => a.id === "doe_plan")?.subtitle).toBe("2 组实验");
    expect(arts.find((a) => a.id === "deep_report")?.modal).toBeNull();
  });

  it("includes wiki_report entry when an active project is set", () => {
    const arts = selectProjectArtifacts({
      ...empty,
      activeProjectId: "proj-1",
    });
    expect(arts).toHaveLength(1);
    expect(arts[0]).toMatchObject({
      id: "wiki_report",
      title: "卷宗 Report",
      modal: "knowledge",
      ready: true,
    });
  });
});

describe("modalForArtifact", () => {
  it("maps kinds to ActionsPanel modals", () => {
    expect(modalForArtifact("leaderboard")).toBe("recommend");
    expect(modalForArtifact("doe_plan")).toBe("doe");
    expect(modalForArtifact("optimization")).toBe("optimize");
    expect(modalForArtifact("loop_report")).toBe("loop");
    expect(modalForArtifact("deep_report")).toBeNull();
    expect(modalForArtifact("wiki_report")).toBe("knowledge");
  });
});
