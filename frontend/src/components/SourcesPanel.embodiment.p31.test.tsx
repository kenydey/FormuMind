/**
 * SourcesPanel P3.1 — KB eligible rows show embodiment extract.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import { noNotificationsDismissed } from "../store/notifications";
import type { AppState } from "../store/types";
import SourcesPanel from "./SourcesPanel";

function setState(patch: Partial<AppState>) {
  useStore.setState({
    searchQuery: "epoxy",
    sourceTypes: ["surechembl"],
    sources: [],
    selectedSources: [],
    sourceStatus: { surechembl: { available: true } },
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

describe("SourcesPanel P3.1 embodiment KB gate", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getSourceStatus").mockResolvedValue({
      surechembl: { available: true },
    } as never);
    vi.spyOn(api, "kbSources").mockResolvedValue({
      sources: [
        {
          id: "src-eligible",
          filename: "a.pdf",
          title: "Fulltext patent",
          source_kind: "patent",
          raw_text_chars: 12000,
          extraction_status: "ok",
        },
        {
          id: "src-short",
          filename: "b.pdf",
          title: "Too short",
          source_kind: "patent",
          raw_text_chars: 20,
          extraction_status: "ok",
        },
      ],
    } as never);
    vi.spyOn(api, "embodimentEligibility").mockResolvedValue({
      items: [
        { source_id: "src-eligible", eligible: true, reason: null },
        { source_id: "src-short", eligible: false, reason: "too_short" },
      ],
    });
    setState({});
  });

  it("shows extract only on eligible KB docs", async () => {
    render(<SourcesPanel />);
    await waitFor(() => expect(api.embodimentEligibility).toHaveBeenCalled());
    expect(screen.getByTestId("embodiment-extract-src-eligible")).toBeInTheDocument();
    expect(screen.queryByTestId("embodiment-extract-src-short")).not.toBeInTheDocument();
  });

  it("opens draft modal after KB extract", async () => {
    vi.spyOn(api, "extractEmbodimentDraft").mockResolvedValue({
      ok: true,
      draft: {
        status: "draft",
        needs_review: true,
        origin: "patent_fulltext",
        source_id: "src-eligible",
        doc_id: "CN104789083B",
        amount_source: "table",
        formulation: {
          name: "草稿",
          domain: "anticorrosion_coating",
          ingredients: [{ name: "Epoxy resin", role: "additive", weight_pct: 70 }],
          warnings: [],
          source: "patent_fulltext",
        },
      },
    });
    render(<SourcesPanel />);
    await waitFor(() => expect(screen.getByTestId("embodiment-extract-src-eligible")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("embodiment-extract-src-eligible"));
    await waitFor(() => expect(screen.getByTestId("embodiment-draft-review")).toBeInTheDocument());
    expect(screen.getByTestId("embodiment-amount-source").textContent).toBe("table");
  });
});
