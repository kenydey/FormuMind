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
      runWikiLint: vi.fn(),
      refreshWikiDossier: vi.fn(),
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

const graphPayload = {
  ok: true as const,
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
  meta: {
    node_count: 2,
    edge_count: 1,
    broken_links: 1,
    orphan_count: 1,
    elapsed_ms: 3,
  },
  insights: {
    orphans: [
      {
        path: "materials/lonely.md",
        label: "Lonely",
        kind: "material",
        degree_in: 0,
        degree_out: 0,
        degree: 0,
      },
    ],
    isolates: [
      {
        path: "materials/lonely.md",
        label: "Lonely",
        kind: "material",
        degree: 0,
      },
    ],
    broken: [
      {
        source: "materials/a.md",
        source_label: "A",
        target: "ghost-x",
        reason: "unresolved",
      },
    ],
    components: { count: 2, largest: 1 },
  },
};

describe("HubWikiGraphPane", () => {
  beforeEach(() => {
    useStore.setState({ activeProjectId: "proj-g1", envFlagsRevision: 0 } as never);
    vi.mocked(api.getEnvFlags).mockResolvedValue({
      flags: [flag("wiki_page_graph_enabled", true)],
    });
    vi.mocked(api.getWikiPageGraph).mockResolvedValue(graphPayload);
    vi.mocked(api.runWikiLint).mockResolvedValue({
      ok: true,
      scanned: 4,
      flagged: 1,
      orphan_count: 1,
    });
    vi.mocked(api.refreshWikiDossier).mockResolvedValue({
      ok: true,
      path: "themes/project-proj-g1.md",
      patched_sections: ["S1_requirements"],
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

  it("insights sidebar opens orphan and runs lint", async () => {
    const user = userEvent.setup();
    const onOpenPath = vi.fn();
    render(<HubWikiGraphPane active onOpenPath={onOpenPath} />);
    expect(await screen.findByTestId("hub-wiki-graph-insights")).toBeInTheDocument();
    expect(screen.getByTestId("hub-wiki-graph-orphan-materials/lonely.md")).toBeInTheDocument();
    expect(screen.getByTestId("hub-wiki-graph-broken-0").textContent).toMatch(/ghost-x/);

    await user.click(screen.getByTestId("hub-wiki-graph-orphan-open-materials/lonely.md"));
    expect(onOpenPath).toHaveBeenCalledWith("materials/lonely.md");

    await user.click(screen.getByTestId("hub-wiki-graph-run-lint"));
    await waitFor(() => {
      expect(api.runWikiLint).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByTestId("hub-wiki-graph-action-msg").textContent).toMatch(/Lint/);
    });
  });

  it("refresh dossier action opens dossier path", async () => {
    const user = userEvent.setup();
    const onOpenPath = vi.fn();
    render(<HubWikiGraphPane active onOpenPath={onOpenPath} />);
    await screen.findByTestId("hub-wiki-graph-refresh-dossier");
    await user.click(screen.getByTestId("hub-wiki-graph-refresh-dossier"));
    await waitFor(() => {
      expect(api.refreshWikiDossier).toHaveBeenCalledWith({ project_id: "proj-g1" });
    });
    await waitFor(() => {
      expect(onOpenPath).toHaveBeenCalledWith("themes/project-proj-g1.md");
    });
  });
});
