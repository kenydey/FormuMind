import { describe, expect, it } from "vitest";
import { withActiveProjectId } from "./withActiveProjectId";

describe("withActiveProjectId", () => {
  it("injects active project when requirement.project_id is empty", () => {
    expect(withActiveProjectId({ domain: "x", project_id: "" }, "proj-1")).toEqual({
      domain: "x",
      project_id: "proj-1",
    });
    expect(withActiveProjectId({ project_id: undefined }, "proj-2").project_id).toBe("proj-2");
  });

  it("does not overwrite an existing project_id", () => {
    expect(withActiveProjectId({ project_id: "keep-me" }, "other")).toEqual({
      project_id: "keep-me",
    });
  });

  it("no-ops when both are empty", () => {
    expect(withActiveProjectId({ project_id: "" }, null)).toEqual({ project_id: "" });
    expect(withActiveProjectId({}, undefined)).toEqual({});
  });
});
