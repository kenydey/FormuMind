import { describe, expect, it } from "vitest";
import {
  idsMatch,
  normalizePatentPub,
  patentIdAliases,
} from "./patentIds";

describe("patentIds", () => {
  it("normalizes SCPN hyphen / compact / Google Patents URL", () => {
    expect(normalizePatentPub("CN-104789083-B")).toBe("CN104789083B");
    expect(normalizePatentPub("CN104789083B")).toBe("CN104789083B");
    expect(normalizePatentPub("https://patents.google.com/patent/CN104789083B")).toBe(
      "CN104789083B"
    );
  });

  it("builds aliases that cross-match", () => {
    const aliases = patentIdAliases("CN-104789083-B");
    expect(aliases).toContain("CN-104789083-B");
    expect(aliases).toContain("CN104789083B");
    expect(aliases.some((a) => a.includes("patents.google.com"))).toBe(true);
    expect(idsMatch("CN-104789083-B", "CN104789083B")).toBe(true);
    expect(idsMatch("CN-104789083-B", "US123")).toBe(false);
  });
});
