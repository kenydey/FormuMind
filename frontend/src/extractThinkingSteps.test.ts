import { describe, expect, it } from "vitest";
import { extractThinkingSteps, type TaskProgressEvent } from "./api";

describe("extractThinkingSteps", () => {
  it("returns empty for missing data", () => {
    expect(extractThinkingSteps(undefined)).toEqual([]);
    expect(extractThinkingSteps({ status: "RUNNING", message: "x" })).toEqual([]);
  });

  it("maps thinking snapshot rows", () => {
    const ev: TaskProgressEvent = {
      status: "RUNNING",
      message: "评估",
      stage: "grade",
      data: {
        thinking: [
          { id: "retrieve", title: "检索", status: "done" },
          { id: "grade", title: "评估", detail: "CRAG", status: "running" },
        ],
      },
    };
    const steps = extractThinkingSteps(ev);
    expect(steps).toHaveLength(2);
    expect(steps[0].status).toBe("done");
    expect(steps[1].detail).toBe("CRAG");
  });
});
