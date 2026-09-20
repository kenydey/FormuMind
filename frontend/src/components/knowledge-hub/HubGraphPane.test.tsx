import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import { useStore } from "../../store";
import HubGraphPane from "./HubGraphPane";

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      kgStats: vi.fn(),
      kgGraph: vi.fn(),
      neo4jStats: vi.fn(),
      neo4jCompounds: vi.fn(),
      kgResolve: vi.fn(),
      kgSubstitutes: vi.fn(),
      kgContradictions: vi.fn(),
      kgRelations: vi.fn(),
      kgFeedbackReport: vi.fn(),
      kgFeedbackStats: vi.fn(),
    },
  };
});

describe("HubGraphPane materials canvas", () => {
  beforeEach(() => {
    useStore.setState({ activeProjectId: "proj-kg-1" } as never);
    vi.mocked(api.kgGraph).mockResolvedValue({
      ok: true,
      nodes: [
        {
          id: "chem:b",
          label: "Zinc phosphate",
          kind: "chemical",
          degree: 1,
          degree_out: 1,
          degree_in: 0,
        },
        {
          id: "chem:a",
          label: "Chromate",
          kind: "chemical",
          degree: 1,
          degree_in: 1,
          degree_out: 0,
        },
      ],
      edges: [
        {
          source: "chem:b",
          target: "chem:a",
          weight: 0.9,
          relation_type: "substitutes",
        },
      ],
      meta: {
        node_count: 2,
        edge_count: 1,
        backend: "sqlite",
        relation_types: ["substitutes", "measured_performance"],
        scanned_links: 1,
        elapsed_ms: 2,
      },
    });
    vi.mocked(api.kgStats).mockResolvedValue({
      enabled: true,
      entities: 2,
      mentions: 0,
      links: 1,
    });
    vi.mocked(api.neo4jStats).mockRejectedValue(new Error("neo off"));
    vi.mocked(api.neo4jCompounds).mockResolvedValue([]);
    vi.mocked(api.kgResolve).mockResolvedValue({
      chemicals: [{ id: "chem:b", canonical_name: "Zinc phosphate" }],
      trade_products: [],
      top_relations: [],
    } as never);
    vi.mocked(api.kgSubstitutes).mockResolvedValue({ entity_id: "chem:b", substitutes: [] } as never);
    vi.mocked(api.kgContradictions).mockResolvedValue({ entity_id: "chem:b", items: [] } as never);
    vi.mocked(api.kgRelations).mockResolvedValue([]);
    vi.mocked(api.kgFeedbackReport).mockResolvedValue({ alert: null } as never);
    vi.mocked(api.kgFeedbackStats).mockResolvedValue({
      measured_total: 0,
      measured_performance: 0,
    } as never);
  });

  it("loads canvas by default and selects entity on node click", async () => {
    const user = userEvent.setup();
    render(<HubGraphPane active />);
    expect(await screen.findByTestId("hub-graph-pane")).toBeInTheDocument();
    expect(screen.getByTestId("hub-graph-view-canvas")).toBeInTheDocument();
    await waitFor(() => expect(api.kgGraph).toHaveBeenCalled());
    expect(await screen.findByTestId("wiki-graph-node-chem:b")).toBeInTheDocument();
    await user.click(screen.getByTestId("wiki-graph-node-chem:b"));
    expect(await screen.findByTestId("hub-graph-entity-panel")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("hub-graph-entity-panel").textContent).toMatch(/Zinc phosphate|chem:b/);
    });
  });

  it("switches to stats mode", async () => {
    const user = userEvent.setup();
    render(<HubGraphPane active />);
    await screen.findByTestId("hub-graph-canvas-mode");
    await user.click(screen.getByTestId("hub-graph-view-stats"));
    await waitFor(() => expect(api.kgStats).toHaveBeenCalled());
    expect(await screen.findByTestId("hub-graph-stats-mode")).toBeInTheDocument();
  });
});
