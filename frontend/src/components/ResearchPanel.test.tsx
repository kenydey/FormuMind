/**
 * The deep-research stage strip.
 *
 * `CRAG_STAGE_IDS` has to cover every stage the backend emits in deep mode,
 * because `stageIndex` falls back to 0 for anything it does not recognise. That
 * turned a report rewrite into a progress bar jumping back to 检索 — the run
 * looked like it had restarted from scratch.
 *
 * The list is asserted here and in `backend/tests/test_deep_research_depth.py`
 * (`test_every_deep_stage_is_known_to_the_frontend`). Two assertions across the
 * boundary is the point: adding a stage on either side fails on the other.
 */
import { describe, expect, it } from "vitest";
import { CRAG_STAGE_IDS } from "./ResearchPanel";

/** Every stage `research_graph._emit` sends on the deep path. */
const BACKEND_DEEP_STAGES = [
  "retrieve",
  "grade",
  "fallback",
  "generate",
  "claim_check",
  "regenerate",
] as const;

describe("CRAG stage contract", () => {
  it("knows every stage deep research emits", () => {
    for (const stage of BACKEND_DEEP_STAGES) {
      expect(CRAG_STAGE_IDS).toContain(stage);
    }
  });

  it("includes regenerate, whose absence made the bar jump backwards", () => {
    // The specific regression: emitted by the claim-check retry loop, missing
    // from the list, so stageIndex returned 0 and the bar reset to the first step.
    expect(CRAG_STAGE_IDS).toContain("regenerate");
  });

  it("does not advertise stages deep research never reaches", () => {
    // `recommend` is emitted only by the recommend-mode pipeline, which does not
    // render this strip. Listing it here would show a step that never lights up.
    expect(CRAG_STAGE_IDS).not.toContain("recommend");
  });

  it("orders the stages the way the pipeline runs them", () => {
    const order = (id: string) => CRAG_STAGE_IDS.indexOf(id);
    expect(order("retrieve")).toBeLessThan(order("grade"));
    expect(order("grade")).toBeLessThan(order("generate"));
    expect(order("generate")).toBeLessThan(order("claim_check"));
    // A rewrite happens after verification, so it must not sit before it.
    expect(order("claim_check")).toBeLessThan(order("regenerate"));
  });

  it("has no duplicates", () => {
    expect(new Set(CRAG_STAGE_IDS).size).toBe(CRAG_STAGE_IDS.length);
  });
});
