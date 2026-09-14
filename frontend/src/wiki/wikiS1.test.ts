import { describe, expect, it } from "vitest";
import { parseWikiFrontMatter } from "./frontMatter";
import {
  buildWikiLinkIndex,
  deadWikiTip,
  parseWikiHref,
  resolveWikiLink,
  rewriteWikiLinks,
} from "./wikilinks";
import {
  isWikiEvidence,
  relatedWikiFromCitations,
  wikiPathFromIdentifier,
} from "./wikiEvidence";
import type { Evidence } from "../api";

describe("parseWikiFrontMatter", () => {
  it("splits meta and body", () => {
    const md = `---
kind: material
title: "E-51"
source_ids: ["s1", "s2"]
flags: ["stale"]
---
# E-51

Body with $$E=mc^2$$.
`;
    const { meta, body } = parseWikiFrontMatter(md);
    expect(meta.kind).toBe("material");
    expect(meta.title).toBe("E-51");
    expect(meta.source_ids).toEqual(["s1", "s2"]);
    expect(meta.flags).toEqual(["stale"]);
    expect(body).toContain("# E-51");
    expect(body).not.toContain("source_ids");
  });
});

describe("wikilinks", () => {
  const index = buildWikiLinkIndex([
    { path: "materials/e51.md", title: "E-51", norm_key: "e51", kind: "material" },
    { path: "systems/epoxy.md", title: "环氧体系", kind: "system" },
  ]);

  it("resolves by title / path / norm_key", () => {
    expect(resolveWikiLink("E-51", index)?.path).toBe("materials/e51.md");
    expect(resolveWikiLink("materials/e51.md", index)?.path).toBe("materials/e51.md");
    expect(resolveWikiLink("e51", index)?.path).toBe("materials/e51.md");
  });

  it("resolves [[kind:key]] and rewrites live/dead links", () => {
    expect(resolveWikiLink("material:e51", index)?.path).toBe("materials/e51.md");
    const out = rewriteWikiLinks(
      "See [[material:e51|牌号]] and [[Missing|缺页]] and [[system:epoxy|体系]].",
      index,
    );
    expect(out).toContain("wiki-path:");
    expect(out).toContain("wiki-dead:");
    expect(out).toContain(encodeURIComponent("materials/e51.md"));
    expect(parseWikiHref(`wiki-path:${encodeURIComponent("materials/e51.md")}`)).toEqual({
      kind: "path",
      value: "materials/e51.md",
    });
    expect(parseWikiHref("wiki-dead:Missing")?.kind).toBe("dead");
    expect(deadWikiTip("material:missing")).toContain("未编译");
  });
});

describe("wikiEvidence", () => {
  it("detects wiki citations and extracts paths", () => {
    const citations: Evidence[] = [
      {
        source: "wiki",
        identifier: "wiki:materials/e51.md",
        title: "[Wiki/material] E-51",
        snippet: "epoxy",
        relevance: 0.9,
      },
      {
        source: "patent",
        identifier: "US123",
        title: "Raw patent",
        snippet: "claim",
        relevance: 0.8,
      },
    ];
    expect(isWikiEvidence(citations[0])).toBe(true);
    expect(isWikiEvidence(citations[1])).toBe(false);
    expect(wikiPathFromIdentifier("wiki:materials/e51.md")).toBe("materials/e51.md");
    const related = relatedWikiFromCitations(citations);
    expect(related).toHaveLength(1);
    expect(related[0].title).toBe("E-51");
    expect(related[0].snippet).toBe("epoxy");
  });
});
