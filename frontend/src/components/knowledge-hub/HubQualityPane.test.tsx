import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HubQualityPane from "./HubQualityPane";

vi.mock("../../api", () => ({
  api: {
    kbQualityOps: vi.fn(async () => ({
      kb_quality_score: 72.5,
      kb_quality_components: { embed_coverage: 24, scan_headroom: 18 },
      sources_active: 12,
      sources_archived: 2,
      scan_pressure: 0.12,
      scan_near_cap: false,
      vector_mode: "semantic",
      topicality_would_reject_pct: 8,
      embedded_chunks: 100,
      chunks_active: 120,
      relevance_shadow: { batch_count: 3 },
      notes: ["kb_quality_score 为只读启发式，不改变入库/检索行为。"],
    })),
  },
  formatApiError: (e: unknown) => String(e),
}));

vi.mock("../../store", () => ({
  useStore: (sel: (s: { activeProjectId: string }) => unknown) =>
    sel({ activeProjectId: "p1" }),
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
    expect(await screen.findByTestId("hub-quality-score")).toHaveTextContent("73");
    expect(screen.getByTestId("hub-quality-components")).toHaveTextContent("embed_coverage");
    expect(screen.getByTestId("mock-probe")).toHaveTextContent("on");
  });
});
