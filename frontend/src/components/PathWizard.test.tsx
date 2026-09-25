import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PathWizard from "./PathWizard";

const setOpenModal = vi.fn();
const openKnowledgeHub = vi.fn();
const refreshWorkbenchStats = vi.fn();
let activeProjectId: string | null = "proj-1";

vi.mock("../store", () => ({
  useStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ setOpenModal, openKnowledgeHub, refreshWorkbenchStats, activeProjectId }),
}));

describe("PathWizard", () => {
  beforeEach(() => {
    setOpenModal.mockClear();
    openKnowledgeHub.mockClear();
    refreshWorkbenchStats.mockClear();
    activeProjectId = "proj-1";
  });

  it("opens formula path steps into existing modals", () => {
    render(<PathWizard />);
    fireEvent.click(screen.getByTestId("path-card-formula"));
    expect(screen.getByTestId("path-steps-formula")).toBeTruthy();
    fireEvent.click(screen.getByTestId("path-step-formula-0"));
    expect(setOpenModal).toHaveBeenCalledWith("requirements");
    fireEvent.click(screen.getByTestId("path-step-formula-3"));
    expect(setOpenModal).toHaveBeenCalledWith("workbench");
  });

  it("deep-links knowledge quality tab when project active", () => {
    render(<PathWizard />);
    fireEvent.click(screen.getByTestId("path-card-knowledge"));
    fireEvent.click(screen.getByTestId("path-step-knowledge-2"));
    expect(openKnowledgeHub).toHaveBeenCalledWith("quality");
  });

  it("disables knowledge steps without project", () => {
    activeProjectId = null;
    render(<PathWizard />);
    fireEvent.click(screen.getByTestId("path-card-knowledge"));
    expect(screen.getByTestId("path-knowledge-need-project")).toBeTruthy();
    const step = screen.getByTestId("path-step-knowledge-0");
    expect(step).toBeDisabled();
    fireEvent.click(step);
    expect(openKnowledgeHub).not.toHaveBeenCalled();
  });
});
