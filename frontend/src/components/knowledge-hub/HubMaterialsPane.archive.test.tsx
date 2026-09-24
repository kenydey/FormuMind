/**
 * Knowledge Hub materials soft-archive controls (W3).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { api } from "../../api";
import { useStore } from "../../store";
import HubMaterialsPane from "./HubMaterialsPane";

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      kbSources: vi.fn(),
      archiveKbSource: vi.fn(),
      deleteKbSource: vi.fn(),
    },
  };
});

describe("HubMaterialsPane soft-archive", () => {
  beforeEach(() => {
    vi.mocked(api.kbSources).mockReset();
    vi.mocked(api.archiveKbSource).mockReset();
    vi.mocked(api.deleteKbSource).mockReset();
    useStore.setState({
      sources: [],
      selectedSources: [],
      kbIngest: { docs: [] },
      activeProjectId: "p-w3",
    } as never);
    vi.mocked(api.kbSources).mockResolvedValue({
      sources: [
        {
          id: "src-1",
          title: "Epoxy primer",
          filename: "epoxy.pdf",
          source_kind: "paper",
          origin_url: "10.1000/x",
          raw_text_chars: 100,
          extraction_status: "indexed",
          archived: false,
        },
      ],
      total: 1,
    });
    vi.mocked(api.archiveKbSource).mockResolvedValue({
      ok: true,
      source_id: "src-1",
      archived: true,
    });
    window.confirm = vi.fn(() => true);
  });

  it("archives a KB row and refreshes with includeArchived flag", async () => {
    render(<HubMaterialsPane open />);

    expect(await screen.findByText("Epoxy primer")).toBeTruthy();
    const archiveBtn = await screen.findByTestId("hub-materials-archive-src-1");
    expect(archiveBtn.textContent).toMatch(/归档/);
    fireEvent.click(archiveBtn);

    await waitFor(() => {
      expect(api.archiveKbSource).toHaveBeenCalledWith("src-1", true);
    });

    const toggle = screen.getByTestId("hub-materials-include-archived").querySelector("input")!;
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(api.kbSources).toHaveBeenCalledWith("p-w3", 200, { includeArchived: true });
    });
  });
});
