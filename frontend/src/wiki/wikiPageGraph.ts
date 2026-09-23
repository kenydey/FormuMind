import type { WikiPageGraphEdge, WikiPageGraphNode } from "../api";

export type WikiPageGraphFilter = {
  query?: string;
  hideOrphan?: boolean;
  kinds?: string[];
};

export type WikiPageGraphColorMode = "kind" | "community";

/** Pure filter for Hub Wiki link graph (unit-testable). */
export function filterWikiPageGraph(
  nodes: WikiPageGraphNode[],
  edges: WikiPageGraphEdge[],
  filter: WikiPageGraphFilter,
): { nodes: WikiPageGraphNode[]; edges: WikiPageGraphEdge[] } {
  const q = (filter.query || "").trim().toLowerCase();
  const kindSet =
    filter.kinds && filter.kinds.length > 0
      ? new Set(filter.kinds.map((k) => k.toLowerCase()))
      : null;

  let kept = nodes;
  if (kindSet) {
    kept = kept.filter((n) => kindSet.has((n.kind || "").toLowerCase()));
  }
  if (filter.hideOrphan) {
    kept = kept.filter((n) => (n.degree ?? 0) > 0);
  }
  if (q) {
    kept = kept.filter((n) => {
      const hay = `${n.label || ""} ${n.path || ""} ${n.kind || ""}`.toLowerCase();
      return hay.includes(q);
    });
  }

  const ids = new Set(kept.map((n) => n.id || n.path));
  const keptEdges = edges.filter(
    (e) => ids.has(e.source) && ids.has(e.target),
  );
  return { nodes: kept, edges: keptEdges };
}

/** Kind → fill for SVG canvas. */
export function kindColor(kind: string): string {
  switch ((kind || "").toLowerCase()) {
    case "material":
      return "#38bdf8";
    case "chemical":
      return "#a78bfa";
    case "system":
      return "#34d399";
    case "mechanism":
      return "#fbbf24";
    case "pitfall":
      return "#fb7185";
    case "theme":
      return "#f472b6";
    case "report":
      return "#94a3b8";
    default:
      return "#64748b";
  }
}

const COMMUNITY_PALETTE = [
  "#38bdf8",
  "#a78bfa",
  "#34d399",
  "#fbbf24",
  "#fb7185",
  "#f472b6",
  "#2dd4bf",
  "#818cf8",
  "#f97316",
  "#e879f9",
];

/** Stable community id → fill (weak-component communities from API). */
export function communityColor(community: number | undefined | null): string {
  const n = Number.isFinite(community) ? Math.abs(Math.trunc(community as number)) : 0;
  return COMMUNITY_PALETTE[n % COMMUNITY_PALETTE.length];
}

export function nodeFill(
  node: WikiPageGraphNode,
  mode: WikiPageGraphColorMode = "kind",
): string {
  if (mode === "community") return communityColor(node.community);
  return kindColor(node.kind);
}

/** Undirected adjacency for hover highlight (path/id keys). */
export function neighborSet(
  focusId: string,
  edges: WikiPageGraphEdge[],
): Set<string> {
  const out = new Set<string>([focusId]);
  for (const e of edges) {
    if (e.source === focusId) out.add(e.target);
    if (e.target === focusId) out.add(e.source);
  }
  return out;
}

/** Map edge weight (~1–2) to SVG stroke width. */
export function edgeStrokeWidth(weight?: number): number {
  const w = typeof weight === "number" && Number.isFinite(weight) ? weight : 1;
  return Math.max(1, Math.min(4, 0.8 + w * 1.2));
}
