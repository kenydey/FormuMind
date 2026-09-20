import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import { useStore } from "../../store";
import HubWikiPane from "./HubWikiPane";

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listWikiPages: vi.fn(),
      listWikiFlags: vi.fn(),
      getWikiByPath: vi.fn(),
      getWikiDossier: vi.fn(),
      ensureWikiDossier: vi.fn(),
      refreshWikiDossier: vi.fn(),
      searchWikiPages: vi.fn(),
      rebuildWikiFts: vi.fn(),
      rebuildWikiEmbed: vi.fn(),
      compileWikiTheme: vi.fn(),
      reviewWikiPage: vi.fn(),
      runWikiLint: vi.fn(),
      getEnvFlags: vi.fn(),
      getWikiPageGraph: vi.fn(),
    },
  };
});

describe("HubWikiPane dossier controls", () => {
  beforeEach(() => {
    useStore.setState({ activeProjectId: "proj-hub-1" } as never);
    vi.mocked(api.listWikiPages).mockResolvedValue({ pages: [], total: 0 });
    vi.mocked(api.listWikiFlags).mockResolvedValue({ pages: [] });
    vi.mocked(api.runWikiLint).mockResolvedValue({
      ok: true,
      scanned: 3,
      flagged: 1,
      orphan_count: 1,
    });
    vi.mocked(api.getWikiDossier).mockRejectedValue(new Error("not found"));
    vi.mocked(api.ensureWikiDossier).mockResolvedValue({
      ok: true,
      path: "themes/project-proj-hub-1.md",
      project_id: "proj-hub-1",
    });
  });

  it("renders dossier buttons and opens/ensures project dossier", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getWikiDossier)
      .mockRejectedValueOnce(new Error("missing"))
      .mockResolvedValueOnce({
        path: "themes/project-proj-hub-1.md",
        title: "卷宗标题",
        markdown: "# 卷宗\n\n## S1. 技术要求与目标指标\n",
        flags: ["unreviewed"],
        revision: 1,
        data: {
          project_id: "proj-hub-1",
          template: "project_dossier",
          section_revisions: { S1_requirements: 1, S4_doe: 1 },
          flags: { empty_doe: true },
        },
      });

    render(<HubWikiPane active />);
    expect(await screen.findByTestId("hub-wiki-pane")).toBeInTheDocument();
    expect(screen.getByTestId("hub-wiki-open-dossier")).toBeInTheDocument();
    expect(screen.getByTestId("hub-wiki-refresh-dossier")).toBeInTheDocument();

    await user.click(screen.getByTestId("hub-wiki-open-dossier"));
    await waitFor(() => {
      expect(api.ensureWikiDossier).toHaveBeenCalledWith({ project_id: "proj-hub-1" });
    });
    await waitFor(() => {
      expect(screen.getByTestId("hub-wiki-dossier-meta").textContent).toMatch(/section_revisions/);
    });
    expect(screen.getByTestId("hub-wiki-dossier-meta").textContent).toMatch(/S1_requirements/);
  });

  it("refresh dossier calls refresh API when project is active", async () => {
    const user = userEvent.setup();
    vi.mocked(api.refreshWikiDossier).mockResolvedValue({
      ok: true,
      path: "themes/project-proj-hub-1.md",
      patched_sections: ["S1_requirements"],
    });
    vi.mocked(api.getWikiDossier).mockResolvedValue({
      path: "themes/project-proj-hub-1.md",
      title: "卷宗",
      markdown: "# ok",
      flags: [],
      revision: 2,
      data: { section_revisions: { S1_requirements: 2 } },
    });

    render(<HubWikiPane active />);
    await screen.findByTestId("hub-wiki-pane");
    await user.click(screen.getByTestId("hub-wiki-refresh-dossier"));
    await waitFor(() => {
      expect(api.refreshWikiDossier).toHaveBeenCalledWith({ project_id: "proj-hub-1" });
    });
  });

  it("passes project_id when listing wiki pages", async () => {
    vi.mocked(api.listWikiPages).mockResolvedValue({ pages: [], total: 0 });
    render(<HubWikiPane active />);
    await screen.findByTestId("hub-wiki-pane");
    await waitFor(() => {
      expect(api.listWikiPages).toHaveBeenCalledWith(
        expect.objectContaining({ project_id: "proj-hub-1" }),
      );
    });
    expect(screen.getByTestId("hub-wiki-project-scope").textContent).toMatch(/proj-hub-1/);
  });

  it("disables dossier actions without active project", () => {
    useStore.setState({ activeProjectId: null } as never);
    render(<HubWikiPane active />);
    expect(screen.getByTestId("hub-wiki-open-dossier")).toBeDisabled();
    expect(screen.getByTestId("hub-wiki-refresh-dossier")).toBeDisabled();
    expect(screen.getByTestId("hub-wiki-project-scope").textContent).toMatch(/未选择/);
  });

  it("runs lint and shows actionable flag chips", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listWikiFlags).mockResolvedValue({
      pages: [
        {
          id: "f1",
          path: "materials/lonely.md",
          kind: "material",
          title: "lonely",
          flags: ["orphan", "stale"],
          source_ids: [],
          actions: [
            { id: "open_page", label: "打开页面", hint: "materials/lonely.md" },
            { id: "check_sources", label: "核对 source_ids / Evidence", hint: "补文献" },
            { id: "link_from_theme", label: "从综述/卷宗补链", hint: "增加 wikilink" },
          ],
        },
      ],
    });
    vi.mocked(api.getWikiByPath).mockResolvedValue({
      id: "f1",
      path: "materials/lonely.md",
      kind: "material",
      title: "lonely",
      flags: ["orphan", "stale"],
      source_ids: [],
      markdown: "# lonely\n",
      revision: 1,
    });

    render(<HubWikiPane active />);
    await screen.findByTestId("hub-wiki-pane");
    expect(screen.getByTestId("hub-wiki-run-lint")).toBeInTheDocument();

    await user.click(screen.getByTestId("hub-wiki-run-lint"));
    await waitFor(() => {
      expect(api.runWikiLint).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByTestId("hub-wiki-lint-summary").textContent).toMatch(/Lint/);
    });
    await waitFor(() => {
      expect(screen.getByTestId("hub-wiki-flag-actions-materials/lonely.md")).toBeInTheDocument();
    });
    expect(screen.getByTestId("hub-wiki-flag-action-link_from_theme")).toBeInTheDocument();

    await user.click(screen.getByTestId("hub-wiki-flag-action-open_page"));
    await waitFor(() => {
      expect(api.getWikiByPath).toHaveBeenCalledWith("materials/lonely.md");
    });
  });

  it("switches to graph view toggle", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getEnvFlags).mockResolvedValue({
      flags: [
        {
          attr: "wiki_page_graph_enabled",
          env_key: "FORMUMIND_WIKI_PAGE_GRAPH_ENABLED",
          label: "Wiki 页链接图",
          description: "",
          category: "kb",
          category_label: "kb",
          hint: "",
          value: true,
          default: false,
        },
      ],
    });
    vi.mocked(api.getWikiPageGraph).mockResolvedValue({
      ok: true,
      nodes: [],
      edges: [],
      meta: { node_count: 0, edge_count: 0 },
    });
    render(<HubWikiPane active />);
    await screen.findByTestId("hub-wiki-pane");
    expect(screen.getByTestId("hub-wiki-view-toggle")).toBeInTheDocument();
    await user.click(screen.getByTestId("hub-wiki-view-graph"));
    expect(await screen.findByTestId("hub-wiki-graph-pane")).toBeInTheDocument();
  });
});
