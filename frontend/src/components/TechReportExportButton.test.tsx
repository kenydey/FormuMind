import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import TechReportExportButton from "./TechReportExportButton";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      exportTechReport: vi.fn(),
    },
  };
});

describe("TechReportExportButton (W3-13)", () => {
  beforeEach(() => {
    vi.mocked(api.exportTechReport).mockReset();
    useStore.setState({ activeProjectId: "proj-1" } as never);
    // jsdom lacks URL.createObjectURL
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
  });

  it("exports with kind+format and triggers download", async () => {
    const user = userEvent.setup();
    const blob = new Blob(["x"], { type: "application/vnd.openxmlformats" });
    vi.mocked(api.exportTechReport).mockResolvedValue({
      blob,
      filename: "formulation_report_proj-1.docx",
    });
    const clickSpy = vi.fn();
    HTMLAnchorElement.prototype.click = clickSpy;

    render(<TechReportExportButton kind="formulation" />);
    await user.selectOptions(
      screen.getByTestId("tech-report-format-formulation"),
      "pdf"
    );
    await user.click(screen.getByTestId("tech-report-export-btn-formulation"));

    await waitFor(() => {
      expect(api.exportTechReport).toHaveBeenCalledWith({
        kind: "formulation",
        format: "pdf",
        project_id: "proj-1",
      });
    });
    expect(clickSpy).toHaveBeenCalled();
    expect(
      await screen.findByTestId("tech-report-done-formulation")
    ).toBeInTheDocument();
  });

  it("shows error when no project is open", async () => {
    const user = userEvent.setup();
    useStore.setState({ activeProjectId: null } as never);
    render(<TechReportExportButton kind="doe" />);
    await user.click(screen.getByTestId("tech-report-export-btn-doe"));
    expect(await screen.findByTestId("tech-report-error-doe")).toHaveTextContent(
      "请先打开或新建项目"
    );
    expect(api.exportTechReport).not.toHaveBeenCalled();
  });

  it("shows backend error message", async () => {
    const user = userEvent.setup();
    vi.mocked(api.exportTechReport).mockRejectedValue(
      new Error("publication preflight blocked")
    );
    render(<TechReportExportButton kind="optimization" />);
    await user.click(screen.getByTestId("tech-report-export-btn-optimization"));
    expect(
      await screen.findByTestId("tech-report-error-optimization")
    ).toHaveTextContent("publication preflight blocked");
  });
});
