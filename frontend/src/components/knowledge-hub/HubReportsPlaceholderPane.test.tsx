import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import { useStore } from "../../store";
import HubReportsPlaceholderPane from "./HubReportsPlaceholderPane";

const saveTextToProjectShelf = vi.fn();

function flag(attr: string, value: boolean) {
  return {
    attr,
    env_key: `FORMUMIND_${attr.toUpperCase()}`,
    label: attr,
    description: "",
    category: "kb",
    category_label: "知识库",
    hint: "",
    value,
    default: false,
  };
}

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      generateWikiReport: vi.fn(),
      exportWikiReport: vi.fn(),
      listWikiReportTemplates: vi.fn(),
      getEnvFlags: vi.fn(),
    },
  };
});

vi.mock("../../utils/export", async () => {
  const actual = await vi.importActual<typeof import("../../utils/export")>("../../utils/export");
  return {
    ...actual,
    saveTextToProjectShelf: (...args: unknown[]) => saveTextToProjectShelf(...args),
  };
});

describe("HubReportsPlaceholderPane", () => {
  beforeEach(() => {
    useStore.setState({
      activeProjectId: "proj-demo",
      settingsOpen: false,
      settingsTab: "llm",
      settingsEnvFocusAttr: null,
    } as never);
    vi.mocked(api.generateWikiReport).mockReset();
    vi.mocked(api.exportWikiReport).mockReset();
    vi.mocked(api.listWikiReportTemplates).mockReset();
    vi.mocked(api.getEnvFlags).mockReset();
    vi.mocked(api.listWikiReportTemplates).mockResolvedValue({
      templates: [],
      export: { md: true, docx: true, pdf: false, pptx: false },
    });
    vi.mocked(api.getEnvFlags).mockResolvedValue({
      flags: [
        flag("wiki_enabled", true),
        flag("wiki_project_dossier_enabled", true),
        flag("wiki_dossier_report_enabled", true),
      ],
    });
    saveTextToProjectShelf.mockReset();
    saveTextToProjectShelf.mockResolvedValue(undefined);
  });

  it("mentions dossier pack foundation and active project", async () => {
    render(<HubReportsPlaceholderPane />);
    expect(screen.getByTestId("hub-reports-pane")).toBeInTheDocument();
    expect(screen.getByTestId("hub-reports-dossier-hint").textContent).toMatch(/proj-demo/);
    expect(screen.getByText(/DossierPack/i)).toBeInTheDocument();
    expect(screen.getByText(/主读卷宗 S1 \+ S6 \+ S8/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("hub-reports-export-caps").textContent).toMatch(/PDF×/);
    });
    await waitFor(() => {
      expect(screen.getByTestId("hub-reports-flags-ready")).toBeInTheDocument();
    });
  });

  it("shows flag CTA and jumps to settings env when grayscale flags are off", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getEnvFlags).mockResolvedValue({
      flags: [
        flag("wiki_enabled", true),
        flag("wiki_project_dossier_enabled", false),
        flag("wiki_dossier_report_enabled", false),
      ],
    });
    render(<HubReportsPlaceholderPane />);
    await waitFor(() => {
      expect(screen.getByTestId("hub-reports-flags-cta")).toBeInTheDocument();
    });
    expect(screen.getByTestId("hub-reports-flag-wiki_dossier_report_enabled").textContent).toMatch(
      /×/,
    );
    expect(screen.queryByTestId("hub-reports-flags-ready")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("hub-reports-open-env-settings"));
    const s = useStore.getState();
    expect(s.settingsOpen).toBe(true);
    expect(s.settingsTab).toBe("env");
    // First missing among Wiki/卷宗/Report — dossier is first false in this mock.
    expect(s.settingsEnvFocusAttr).toBe("wiki_project_dossier_enabled");
  });

  it("enables generate and export actions when a template is selected", async () => {
    const user = userEvent.setup();
    render(<HubReportsPlaceholderPane />);
    await user.click(screen.getByRole("button", { name: /文献简报/ }));
    const btn = await screen.findByTestId("hub-reports-generate");
    expect(btn).not.toBeDisabled();
    expect(btn.textContent).toMatch(/基于卷宗生成/);
    expect(screen.getByTestId("hub-reports-export-pdf")).toBeDisabled();
    expect(screen.getByTestId("hub-reports-export-docx")).not.toBeDisabled();
    expect(screen.getByTestId("hub-reports-export-pptx")).toBeDisabled();
  });

  it("generate shows draft_not_claims and can save to shelf", async () => {
    const user = userEvent.setup();
    vi.mocked(api.generateWikiReport).mockResolvedValue({
      ok: true,
      path: "reports/project-proj-demo-briefing.md",
      title: "文献简报",
      markdown: "# Briefing\n\ndraft body",
      disclaimer: "draft_not_claims",
      template: "briefing",
    });
    render(<HubReportsPlaceholderPane />);
    await user.click(screen.getByRole("button", { name: /文献简报/ }));
    await user.click(screen.getByTestId("hub-reports-generate"));
    await waitFor(() => {
      expect(api.generateWikiReport).toHaveBeenCalledWith(
        expect.objectContaining({
          project_id: "proj-demo",
          template: "briefing",
          ensure_dossier: true,
          persist: true,
        }),
      );
    });
    expect(await screen.findByTestId("hub-reports-disclaimer")).toHaveTextContent(
      "draft_not_claims",
    );
    expect(screen.getByTestId("hub-reports-result").textContent).toMatch(
      /reports\/project-proj-demo-briefing/,
    );
    await user.click(screen.getByTestId("hub-reports-save-shelf"));
    await waitFor(() => {
      expect(saveTextToProjectShelf).toHaveBeenCalled();
    });
    expect(screen.getByTestId("hub-reports-shelf-msg").textContent).toMatch(/已保存到货架/);
  });

  it("export MD calls exportWikiReport with format md", async () => {
    const user = userEvent.setup();
    vi.mocked(api.exportWikiReport).mockResolvedValue({
      blob: new Blob(["# Briefing\n"], { type: "text/markdown" }),
      filename: "project-proj-demo-briefing.md",
    });
    const createObjectURL = vi.fn(() => "blob:mock-md");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    });
    render(<HubReportsPlaceholderPane />);
    await user.click(screen.getByRole("button", { name: /文献简报/ }));
    await user.click(screen.getByTestId("hub-reports-export-md"));
    await waitFor(() => {
      expect(api.exportWikiReport).toHaveBeenCalledWith(
        expect.objectContaining({
          project_id: "proj-demo",
          template: "briefing",
          format: "md",
          ensure_dossier: true,
        }),
      );
    });
    expect(createObjectURL).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("shows settings jump when generate fails with flag gate error", async () => {
    const user = userEvent.setup();
    vi.mocked(api.generateWikiReport).mockRejectedValue(
      new Error("wiki_dossier_report_enabled is false"),
    );
    render(<HubReportsPlaceholderPane />);
    await user.click(screen.getByRole("button", { name: /文献简报/ }));
    await user.click(screen.getByTestId("hub-reports-generate"));
    expect(await screen.findByTestId("hub-reports-error")).toHaveTextContent(
      /wiki_dossier_report_enabled/,
    );
    await user.click(screen.getByTestId("hub-reports-error-open-env"));
    expect(useStore.getState().settingsOpen).toBe(true);
    expect(useStore.getState().settingsTab).toBe("env");
    expect(useStore.getState().settingsEnvFocusAttr).toBe("wiki_dossier_report_enabled");
  });
});
