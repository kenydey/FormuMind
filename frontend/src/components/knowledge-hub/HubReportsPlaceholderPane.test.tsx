import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import { useStore } from "../../store";
import HubReportsPlaceholderPane from "./HubReportsPlaceholderPane";

const saveTextToProjectShelf = vi.fn();

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      generateWikiReport: vi.fn(),
      exportWikiReport: vi.fn(),
      listWikiReportTemplates: vi.fn(),
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
    useStore.setState({ activeProjectId: "proj-demo" } as never);
    vi.mocked(api.generateWikiReport).mockReset();
    vi.mocked(api.listWikiReportTemplates).mockReset();
    vi.mocked(api.listWikiReportTemplates).mockResolvedValue({
      templates: [],
      export: { md: true, docx: true, pdf: false, pptx: false },
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
    vi.mocked(api.exportWikiReport).mockReset();
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
});
