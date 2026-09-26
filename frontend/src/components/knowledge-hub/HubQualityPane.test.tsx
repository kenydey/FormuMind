import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HubQualityPane from "./HubQualityPane";

vi.mock("../../api", () => ({
  api: {
    kbQualityOps: vi.fn(async () => ({
      kb_quality_score: 42,
      kb_quality_components: { embed_coverage: 10, scan_headroom: 2 },
      sources_active: 12,
      sources_archived: 2,
      scan_pressure: 0.95,
      scan_near_cap: true,
      vector_mode: "semantic",
      embedding_model: "BAAI/bge-small-zh-v1.5",
      topicality_would_reject_pct: 8,
      embedded_chunks: 100,
      chunks_active: 4800,
      hybrid_search_latency: {
        n: 8,
        p95_ms: 900,
        ann_last: true,
        ann_matrix_last: true,
        ann_streak: 2,
      },
      relevance_shadow: { batch_count: 3 },
      notes: ["kb_quality_score 为只读启发式，不改变入库/检索行为。"],
    })),
  },
  formatApiError: (e: unknown) => String(e),
}));

const setKnowledgeHubTab = vi.fn();

vi.mock("../../store", () => ({
  useStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ activeProjectId: "p1", setKnowledgeHubTab }),
}));

vi.mock("./RetrievalProbePanel", () => ({
  default: ({ active }: { active: boolean }) => (
    <div data-testid="mock-probe">{active ? "on" : "off"}</div>
  ),
}));

describe("HubQualityPane", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders quality score and components", async () => {
    render(<HubQualityPane active />);
    expect(await screen.findByTestId("hub-quality-score")).toHaveTextContent("42");
    expect(screen.getByTestId("hub-quality-components")).toHaveTextContent("embed_coverage");
    expect(screen.getByTestId("mock-probe")).toHaveTextContent("on");
    expect(screen.getByTestId("hub-quality-scan-cta")).toBeTruthy();
  });
});
