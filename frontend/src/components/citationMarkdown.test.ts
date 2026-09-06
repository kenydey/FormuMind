import { describe, expect, it } from "vitest";
import {
  evidenceToCitationAnchors,
  hasCitationMarkers,
  splitCitationMarkdown,
} from "./citationMarkdown";
import type { Evidence } from "../api";

describe("citationMarkdown", () => {
  it("detects [^n] markers", () => {
    expect(hasCitationMarkers("plain")).toBe(false);
    expect(hasCitationMarkers("claim[^1].")).toBe(true);
  });

  it("splits answer and footnotes when defs are present", () => {
    const md = "环氧树脂附着力好[^1]。\n\n[^1]: CN123456A — snippet";
    const split = splitCitationMarkdown(md);
    expect(split).not.toBeNull();
    expect(split!.answer).toContain("环氧树脂");
    expect(split!.answer).not.toContain("[^1]:");
    expect(split!.footnotes).toContain("[^1]:");
  });

  it("keeps full content as answer when markers exist without defs", () => {
    const split = splitCitationMarkdown("结论[^1]。");
    expect(split).toEqual({ answer: "结论[^1]。", footnotes: "" });
  });

  it("returns null without markers", () => {
    expect(splitCitationMarkdown("no cites")).toBeNull();
  });

  it("maps Evidence to 1-indexed anchors", () => {
    const citations: Evidence[] = [
      {
        source: "patent",
        identifier: "https://example.com/a",
        title: "Alpha",
        snippet: "snip",
        relevance: 0.9,
      },
    ];
    expect(evidenceToCitationAnchors(citations)).toEqual([
      {
        id: "1",
        title: "Alpha",
        snippet: "snip",
        url: "https://example.com/a",
      },
    ]);
  });
});
