import { describe, expect, it } from "vitest";
import { CANCELABLE_TASK_KINDS, CANCEL_BUTTON_CLASS } from "./useTaskCancel";

describe("useTaskCancel kinds", () => {
  it("lists store + long-task cancelable kinds (Top-5″ #5)", () => {
    expect(CANCELABLE_TASK_KINDS).toContain("loop");
    expect(CANCELABLE_TASK_KINDS).toContain("recommend");
    expect(CANCELABLE_TASK_KINDS).toContain("deep_research");
    expect(CANCELABLE_TASK_KINDS).toContain("wiki_storm_report");
    expect(CANCELABLE_TASK_KINDS).toContain("kg_relations_rebuild");
  });

  it("exports shared cancel button class", () => {
    expect(CANCEL_BUTTON_CLASS).toMatch(/rose/);
  });
});
