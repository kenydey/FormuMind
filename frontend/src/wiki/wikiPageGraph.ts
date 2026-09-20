import type { WikiPageGraphEdge, WikiPageGraphNode } from "../api";

export type WikiPageGraphFilter = {
  query?: string;
  hideOrphan?: boolean;
  kinds?: string[];
};

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

/** Kind → Tailwind-ish stroke/fill for SVG canvas. */
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
