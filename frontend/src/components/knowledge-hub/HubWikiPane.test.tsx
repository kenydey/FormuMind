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
    },
  };
});

describe("HubWikiPane dossier controls", () => {
  beforeEach(() => {
    useStore.setState({ activeProjectId: "proj-hub-1" } as never);
    vi.mocked(api.listWikiPages).mockResolvedValue({ pages: [], total: 0 });
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

  it("disables dossier actions without active project", () => {
    useStore.setState({ activeProjectId: null } as never);
    render(<HubWikiPane active />);
    expect(screen.getByTestId("hub-wiki-open-dossier")).toBeDisabled();
    expect(screen.getByTestId("hub-wiki-refresh-dossier")).toBeDisabled();
  });
});
