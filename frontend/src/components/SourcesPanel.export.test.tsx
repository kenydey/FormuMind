/**
 * ``POST /api/sources/export`` (P1-37) merges up to ten KB documents into one docx / pdf / html /
 * md file. It had a route and a service but no entry point: the KB list now lets the user pick
 * documents and export them.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import { noNotificationsDismissed } from "../store/notifications";
import type { AppState } from "../store/types";
import SourcesPanel from "./SourcesPanel";

const saveBlob = vi.fn();
vi.mock("../utils/download", async () => {
  const actual = await vi.importActual<typeof import("../utils/download")>("../utils/download");
  return { ...actual, saveBlob: (...args: unknown[]) => saveBlob(...args) };
});

function doc(id: string) {
  return {
    id,
    filename: `${id}.pdf`,
    title: `Doc ${id}`,
    source_kind: "patent",
    raw_text_chars: 5000,
    extraction_status: "ok",
  };
}

function setState(patch: Partial<AppState> = {}) {
  useStore.setState({
    searchQuery: "",
    sourceTypes: ["literature"],
    sources: [],
    selectedSources: [],
    sourceStatus: {},
    searchBusy: false,
    searchProgress: null,
    kbIngest: null,
    deepResearchBusy: false,
    deepResearchMessage: "",
    error: null,
    usedSeedFallback: false,
    filterReport: null,
    notificationsDismissed: noNotificationsDismissed(),
    ...patch,
  } as AppState);
}

async function renderWithDocs(ids: string[]) {
  vi.spyOn(api, "kbSources").mockResolvedValue({ sources: ids.map(doc) } as never);
  render(<SourcesPanel />);
  await screen.findByTestId(`kb-export-pick-${ids[0]}`);
}

beforeEach(() => {
  vi.restoreAllMocks();
  saveBlob.mockReset();
  vi.spyOn(api, "getSourceStatus").mockResolvedValue({} as never);
  vi.spyOn(api, "embodimentEligibility").mockResolvedValue({ items: [] });
  vi.spyOn(api, "exportSources").mockResolvedValue({
    blob: new Blob(["x"]),
    filename: "sources_export.docx",
  });
  setState();
});

describe("SourcesPanel KB export", () => {
  it("exports exactly the picked documents, in the chosen format", async () => {
    await renderWithDocs(["a", "b", "c"]);
    const run = screen.getByTestId("kb-export-run") as HTMLButtonElement;
    expect(run.disabled).toBe(true); // nothing picked yet

    fireEvent.click(screen.getByTestId("kb-export-pick-c"));
    fireEvent.click(screen.getByTestId("kb-export-pick-a"));
    fireEvent.change(screen.getByTestId("kb-export-format"), { target: { value: "md" } });
    expect(run.textContent).toContain("(2)");
    fireEvent.click(run);

    await waitFor(() => expect(saveBlob).toHaveBeenCalledTimes(1));
    expect(api.exportSources).toHaveBeenCalledWith({ source_ids: ["c", "a"], format: "md" });
    expect(saveBlob.mock.calls[0][1]).toBe("sources_export.docx");
    expect(screen.getByTestId("kb-export-msg").textContent).toContain("已导出 sources_export.docx");
  });

  it("stops offering more once ten are picked (the endpoint refuses more)", async () => {
    const ids = Array.from({ length: 12 }, (_, i) => `d${i}`);
    await renderWithDocs(ids);
    for (const id of ids.slice(0, 10)) fireEvent.click(screen.getByTestId(`kb-export-pick-${id}`));

    expect((screen.getByTestId("kb-export-pick-d10") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByTestId("kb-export-pick-d0") as HTMLInputElement).disabled).toBe(false); // can still untick

    fireEvent.click(screen.getByTestId("kb-export-pick-d0"));
    expect((screen.getByTestId("kb-export-pick-d10") as HTMLInputElement).disabled).toBe(false);
  });

  it("shows the server's reason and stays usable when the export fails", async () => {
    vi.spyOn(api, "exportSources").mockImplementation(async () => {
      throw new Error("一次最多导出 10 篇");
    });
    await renderWithDocs(["a"]);
    fireEvent.click(screen.getByTestId("kb-export-pick-a"));
    fireEvent.click(screen.getByTestId("kb-export-run"));

    expect((await screen.findByTestId("kb-export-msg")).textContent).toContain("一次最多导出 10 篇");
    expect(saveBlob).not.toHaveBeenCalled();
    expect((screen.getByTestId("kb-export-run") as HTMLButtonElement).disabled).toBe(false);
  });

  it("never sends a pick whose document is no longer listed", async () => {
    await renderWithDocs(["a", "b"]);
    fireEvent.click(screen.getByTestId("kb-export-pick-a"));

    // The list refreshes (e.g. another project): only b is left.
    vi.spyOn(api, "kbSources").mockResolvedValue({ sources: [doc("b")] } as never);
    act(() => {
      useStore.setState({ kbIngest: { taskId: "t2" } as never });
    });
    await waitFor(() => expect(screen.queryByTestId("kb-export-pick-a")).toBeNull());

    fireEvent.click(screen.getByTestId("kb-export-pick-b"));
    fireEvent.click(screen.getByTestId("kb-export-run"));
    await waitFor(() => expect(api.exportSources).toHaveBeenCalled());
    expect(api.exportSources).toHaveBeenCalledWith({ source_ids: ["b"], format: "docx" });
  });
});
