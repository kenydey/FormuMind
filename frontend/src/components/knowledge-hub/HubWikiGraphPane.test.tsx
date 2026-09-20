import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import { useStore } from "../../store";
import HubWikiGraphPane from "./HubWikiGraphPane";

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getEnvFlags: vi.fn(),
      getWikiPageGraph: vi.fn(),
    },
  };
});

function flag(attr: string, value: boolean) {
  return {
    attr,
    env_key: `FORMUMIND_${attr.toUpperCase()}`,
    label: attr,
    description: "",
    category: "kb",
    category_label: "kb",
    hint: "",
    value,
    default: false,
  };
}

describe("HubWikiGraphPane", () => {
  beforeEach(() => {
    useStore.setState({ activeProjectId: "proj-g1" } as never);
    vi.mocked(api.getEnvFlags).mockResolvedValue({
      flags: [flag("wiki_page_graph_enabled", true)],
    });
    vi.mocked(api.getWikiPageGraph).mockResolvedValue({
      ok: true,
      nodes: [
        {
          id: "materials/a.md",
          path: "materials/a.md",
          label: "A",
          kind: "material",
          degree: 1,
        },
        {
          id: "materials/lonely.md",
          path: "materials/lonely.md",
          label: "Lonely",
          kind: "material",
          degree: 0,
        },
      ],
      edges: [{ source: "materials/a.md", target: "materials/a.md", weight: 1 }],
      meta: { node_count: 2, edge_count: 1, broken_links: 0, elapsed_ms: 3 },
    });
  });

  it("loads graph and opens path on node click", async () => {
    const user = userEvent.setup();
    const onOpenPath = vi.fn();
    render(<HubWikiGraphPane active selectedPath={null} onOpenPath={onOpenPath} />);
    expect(await screen.findByTestId("hub-wiki-graph-pane")).toBeInTheDocument();
    await waitFor(() => {
      expect(api.getWikiPageGraph).toHaveBeenCalled();
    });
    expect(await screen.findByTestId("wiki-graph-node-materials/a.md")).toBeInTheDocument();
    await user.click(screen.getByTestId("wiki-graph-node-materials/a.md"));
    expect(onOpenPath).toHaveBeenCalledWith("materials/a.md");
  });

  it("hide orphan reduces visible nodes", async () => {
    const user = userEvent.setup();
    render(<HubWikiGraphPane active onOpenPath={vi.fn()} />);
    await screen.findByTestId("wiki-graph-node-materials/lonely.md");
    await user.click(screen.getByTestId("hub-wiki-graph-hide-orphan"));
    await waitFor(() => {
      expect(screen.queryByTestId("wiki-graph-node-materials/lonely.md")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("wiki-graph-node-materials/a.md")).toBeInTheDocument();
  });

  it("shows flag CTA when page graph disabled", async () => {
    vi.mocked(api.getWikiPageGraph).mockClear();
    vi.mocked(api.getEnvFlags).mockResolvedValue({
      flags: [flag("wiki_page_graph_enabled", false)],
    });
    render(<HubWikiGraphPane active onOpenPath={vi.fn()} />);
    expect(await screen.findByTestId("hub-wiki-graph-flag-cta")).toBeInTheDocument();
    expect(api.getWikiPageGraph).not.toHaveBeenCalled();
  });
});
