import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ProjectNotebookLMModal from "./ProjectNotebookLMModal";
import { api } from "../api";
import { useStore } from "../store";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      notebooklmStatus: vi.fn(),
      notebooklmConfig: vi.fn(),
      notebooklmLogin: vi.fn(),
    },
  };
});

describe("ProjectNotebookLMModal", () => {
  beforeEach(() => {
    useStore.setState({
      openModal: "notebooklm-setup",
      notebooklmNotebookId: "",
    } as never);
    vi.mocked(api.notebooklmStatus).mockResolvedValue({
      available: true,
      auth_ready: true,
      lib_installed: true,
      enabled: true,
      session_present: true,
      notebook_id_set: false,
      notebook_id: null,
    });
  });

  it("saves notebook id onto the current project workspace", async () => {
    render(<ProjectNotebookLMModal />);
    expect(await screen.findByTestId("project-notebooklm-modal")).toBeTruthy();
    fireEvent.change(screen.getByTestId("project-notebooklm-id"), {
      target: { value: "proj-nb-42" },
    });
    fireEvent.click(screen.getByText("保存到本项目"));
    expect(useStore.getState().notebooklmNotebookId).toBe("proj-nb-42");
    expect(useStore.getState().openModal).toBeNull();
  });
});
