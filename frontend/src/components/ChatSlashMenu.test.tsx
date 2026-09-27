import { describe, expect, it } from "vitest";
import { slashQuery } from "./ChatSlashMenu";

describe("slashQuery", () => {
  it("detects trailing slash token", () => {
    expect(slashQuery("/")).toEqual({ active: true, query: "", prefix: "" });
    expect(slashQuery("/lit")).toEqual({ active: true, query: "lit", prefix: "" });
    expect(slashQuery("hello /coat")).toEqual({
      active: true,
      query: "coat",
      prefix: "hello ",
    });
  });

  it("inactive when slash not at token boundary end", () => {
    expect(slashQuery("a/b")).toEqual({ active: false, query: "", prefix: "a/b" });
    expect(slashQuery("hello ")).toEqual({ active: false, query: "", prefix: "hello " });
  });
});
