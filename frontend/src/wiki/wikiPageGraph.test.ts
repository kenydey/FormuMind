import { describe, expect, it } from "vitest";
import { filterWikiPageGraph, kindColor } from "./wikiPageGraph";
import type { WikiPageGraphEdge, WikiPageGraphNode } from "../api";

const nodes: WikiPageGraphNode[] = [
  { id: "a", path: "materials/a.md", label: "Alpha", kind: "material", degree: 2 },
  { id: "b", path: "materials/b.md", label: "Beta", kind: "material", degree: 1 },
  { id: "c", path: "themes/c.md", label: "Theme C", kind: "theme", degree: 0 },
];

const edges: WikiPageGraphEdge[] = [
  { source: "a", target: "b", weight: 1 },
];

describe("filterWikiPageGraph", () => {
  it("hides orphans when requested", () => {
    const out = filterWikiPageGraph(nodes, edges, { hideOrphan: true });
    expect(out.nodes.map((n) => n.id)).toEqual(["a", "b"]);
    expect(out.edges).toHaveLength(1);
  });

  it("filters by query on label/path", () => {
    const out = filterWikiPageGraph(nodes, edges, { query: "theme" });
    expect(out.nodes).toHaveLength(1);
    expect(out.nodes[0].path).toBe("themes/c.md");
    expect(out.edges).toHaveLength(0);
  });

  it("filters by kind", () => {
    const out = filterWikiPageGraph(nodes, edges, { kinds: ["theme"] });
    expect(out.nodes.map((n) => n.kind)).toEqual(["theme"]);
  });
});

describe("kindColor", () => {
  it("returns stable colors", () => {
    expect(kindColor("material")).toMatch(/^#/);
    expect(kindColor("unknown-xyz")).toMatch(/^#/);
  });
});
