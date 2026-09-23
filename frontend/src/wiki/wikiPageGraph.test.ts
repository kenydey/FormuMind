import { describe, expect, it } from "vitest";
import {
  communityColor,
  edgeStrokeWidth,
  filterWikiPageGraph,
  kindColor,
  neighborSet,
  nodeFill,
} from "./wikiPageGraph";
import type { WikiPageGraphEdge, WikiPageGraphNode } from "../api";

const nodes: WikiPageGraphNode[] = [
  {
    id: "a",
    path: "materials/a.md",
    label: "Alpha",
    kind: "material",
    degree: 2,
    community: 0,
  },
  {
    id: "b",
    path: "materials/b.md",
    label: "Beta",
    kind: "material",
    degree: 1,
    community: 0,
  },
  {
    id: "c",
    path: "themes/c.md",
    label: "Theme C",
    kind: "theme",
    degree: 0,
    community: 1,
  },
];

const edges: WikiPageGraphEdge[] = [
  { source: "a", target: "b", weight: 1.5 },
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

describe("kindColor / communityColor / nodeFill", () => {
  it("returns stable colors", () => {
    expect(kindColor("material")).toMatch(/^#/);
    expect(kindColor("unknown-xyz")).toMatch(/^#/);
    expect(communityColor(0)).toMatch(/^#/);
    expect(communityColor(99)).toMatch(/^#/);
  });

  it("nodeFill switches by mode", () => {
    expect(nodeFill(nodes[0], "kind")).toBe(kindColor("material"));
    expect(nodeFill(nodes[2], "community")).toBe(communityColor(1));
  });
});

describe("neighborSet / edgeStrokeWidth", () => {
  it("includes focus and undirected neighbors", () => {
    const s = neighborSet("a", edges);
    expect(s.has("a")).toBe(true);
    expect(s.has("b")).toBe(true);
    expect(s.has("c")).toBe(false);
  });

  it("maps weight to stroke width band", () => {
    expect(edgeStrokeWidth(1)).toBeGreaterThanOrEqual(1);
    expect(edgeStrokeWidth(2)).toBeGreaterThan(edgeStrokeWidth(1));
    expect(edgeStrokeWidth(99)).toBeLessThanOrEqual(4);
  });
});
