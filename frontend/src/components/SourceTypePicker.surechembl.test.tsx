import { describe, expect, it } from "vitest";
import { SOURCE_TYPES, searchSourceTypes } from "./SourceTypePicker";

describe("SourceTypePicker surechembl P2", () => {
  it("lists surechembl as a selectable research source", () => {
    expect(SOURCE_TYPES.some((t) => t.id === "surechembl")).toBe(true);
  });

  it("keeps surechembl in searchSourceTypes (unlike local)", () => {
    expect(searchSourceTypes(["patents", "surechembl", "local"])).toEqual([
      "patents",
      "surechembl",
    ]);
  });
});
