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
      rebuildWikiCatalog: vi.fn(),
      downloadWikiCatalogMd: vi.fn(),
      compileWikiTheme: vi.fn(),
      reviewWikiPage: vi.fn(),
      runWikiLint: vi.fn(),
      sweepWikiLint: vi.fn(),
      applyWikiBrokenFix: vi.fn(),
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
    vi.mocked(api.getEnvFlags).mockResolvedValue({
      flags: [
        {
          attr: "wiki_embed_enabled",
          env_key: "FORMUMIND_WIKI_EMBED_ENABLED",
          label: "Wiki embed",
          description: "",
          category: "kb",
          category_label: "kb",
          hint: "",
          value: false,
          default: false,
        },
      ],
    });
    vi.mocked(api.runWikiLint).mockResolvedValue({
      ok: true,
      scanned: 3,
      flagged: 1,
      orphan_count: 1,
      broken_count: 0,
    });
    vi.mocked(api.sweepWikiLint).mockResolvedValue({
      ok: true,
      scanned: 2,
      cleared: 1,
      still_flagged: 1,
      orphan_count: 0,
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
          norm_key: "lonely",
          flags: ["orphan", "stale"],
          source_ids: [],
          actions: [
            { id: "open_page", label: "打开页面", hint: "materials/lonely.md", target: "materials/lonely.md" },
            { id: "check_sources", label: "核对 Evidence", hint: "补文献", target: "materials/lonely.md" },
            {
              id: "link_from_theme",
              label: "打开补链候选：Dossier",
              hint: "在候选页手动增加 [[lonely]]",
              target: "themes/project-demo.md",
            },
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
    expect(screen.getByTestId("hub-wiki-sweep-lint")).toBeInTheDocument();

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

  it("link_from_theme opens candidate target path", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listWikiFlags).mockResolvedValue({
      pages: [
        {
          id: "f1",
          path: "materials/lonely.md",
          kind: "material",
          title: "lonely",
          norm_key: "lonely",
          flags: ["orphan"],
          source_ids: ["s1"],
          actions: [
            {
              id: "link_from_theme",
              label: "打开补链候选：Dossier",
              hint: "手动补链",
              target: "themes/project-demo.md",
            },
          ],
        },
      ],
    });
    vi.mocked(api.getWikiByPath).mockImplementation(async (path: string) => ({
      id: path,
      path,
      kind: path.startsWith("themes/") ? "theme" : "material",
      title: path,
      flags: [],
      source_ids: [],
      markdown: `# ${path}\n`,
      revision: 1,
    }));

    render(<HubWikiPane active />);
    await screen.findByTestId("hub-wiki-pane");
    await user.click(screen.getByTestId("hub-wiki-run-lint"));
    await waitFor(() => {
      expect(screen.getByTestId("hub-wiki-flag-action-link_from_theme")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("hub-wiki-flag-action-link_from_theme"));
    await waitFor(() => {
      expect(api.getWikiByPath).toHaveBeenCalledWith("themes/project-demo.md");
    });
  });

  it("sweep lint clears obsolete flags and refreshes list", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listWikiFlags).mockResolvedValue({
      pages: [
        {
          id: "f2",
          path: "materials/still.md",
          kind: "material",
          title: "still",
          norm_key: "still",
          flags: ["stale"],
          source_ids: [],
          actions: [{ id: "open_page", label: "打开页面", target: "materials/still.md" }],
        },
      ],
    });

    render(<HubWikiPane active />);
    await screen.findByTestId("hub-wiki-pane");
    await user.click(screen.getByTestId("hub-wiki-sweep-lint"));
    await waitFor(() => {
      expect(api.sweepWikiLint).toHaveBeenCalledWith({ limit: 200, detect_orphan: true });
    });
    await waitFor(() => {
      expect(screen.getByTestId("hub-wiki-lint-summary").textContent).toMatch(/Sweep/);
    });
    await waitFor(() => {
      expect(screen.getByTestId("hub-wiki-flag-actions-materials/still.md")).toBeInTheDocument();
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

  it("apply_broken_fix chip calls apply API and refreshes flags", async () => {
    const user = userEvent.setup();
    vi.mocked(api.applyWikiBrokenFix).mockResolvedValue({
      ok: true,
      path: "materials/a.md",
      broken: "zinc phosphat",
      replacement_path: "materials/zinc-phosphate.md",
      wikilink: "[[material:zinc-phosphate|Zinc Phosphate]]",
      mode: "rewrite",
      flags: [],
    });
    const flagged = {
      pages: [
        {
          id: "f-broken",
          path: "materials/a.md",
          kind: "material",
          title: "A",
          norm_key: "a",
          flags: ["broken"],
          source_ids: [] as string[],
          actions: [
            { id: "fix_broken", label: "查看断链", target: "materials/a.md", hint: "断链" },
            {
              id: "apply_broken_fix_1",
              label: "改链→ Zinc Phosphate",
              hint: "[[zinc phosphat]] → [[material:zinc-phosphate|Zinc Phosphate]]",
              target: "materials/a.md",
              broken: "zinc phosphat",
              replacement_path: "materials/zinc-phosphate.md",
              mode: "rewrite",
            },
          ],
        },
      ],
    };
    vi.mocked(api.listWikiFlags).mockResolvedValue(flagged);
    vi.mocked(api.getWikiByPath).mockResolvedValue({
      id: "f-broken",
      path: "materials/a.md",
      kind: "material",
      title: "A",
      flags: [],
      source_ids: [],
      markdown: "# A\n\nSee [[material:zinc-phosphate|Zinc Phosphate]]\n",
      revision: 2,
    });

    render(<HubWikiPane active />);
    await screen.findByTestId("hub-wiki-pane");
    await user.click(screen.getByTestId("hub-wiki-run-lint"));
    await waitFor(() => {
      expect(screen.getByTestId("hub-wiki-flag-action-apply_broken_fix_1")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("hub-wiki-flag-action-apply_broken_fix_1"));
    await waitFor(() => {
      expect(api.applyWikiBrokenFix).toHaveBeenCalledWith({
        path: "materials/a.md",
        broken: "zinc phosphat",
        replacement_path: "materials/zinc-phosphate.md",
        mode: "rewrite",
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("hub-wiki-lint-summary").textContent).toMatch(/已修复断链/);
    });
  });

  it("rebuilds and downloads wiki catalog", async () => {
    const user = userEvent.setup();
    vi.mocked(api.rebuildWikiCatalog).mockResolvedValue({
      ok: true,
      entry_count: 3,
      persisted: true,
      path: "catalog.md",
    });
    vi.mocked(api.downloadWikiCatalogMd).mockResolvedValue({
      blob: new Blob(["# Wiki Catalog\n"], { type: "text/markdown" }),
      filename: "catalog.md",
    });
    const createObjectURL = vi.fn(() => "blob:mock-catalog");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    });

    render(<HubWikiPane active />);
    await screen.findByTestId("hub-wiki-pane");
    expect(screen.getByTestId("hub-wiki-rebuild-catalog")).toBeInTheDocument();
    expect(screen.getByTestId("hub-wiki-download-catalog")).toBeInTheDocument();

    await user.click(screen.getByTestId("hub-wiki-rebuild-catalog"));
    await waitFor(() => {
      expect(api.rebuildWikiCatalog).toHaveBeenCalledWith(
        expect.objectContaining({ persist: true, project_id: "proj-hub-1" }),
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("hub-wiki-lint-summary").textContent).toMatch(/Catalog/);
    });

    await user.click(screen.getByTestId("hub-wiki-download-catalog"));
    await waitFor(() => {
      expect(api.downloadWikiCatalogMd).toHaveBeenCalled();
    });
    expect(createObjectURL).toHaveBeenCalled();
  });
});
