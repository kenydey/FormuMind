/**
 * SourcesPanel renders Google Patents / SureChEMBL links on evidence rows.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import { noNotificationsDismissed } from "../store/notifications";
import type { AppState } from "../store/types";
import SourcesPanel from "./SourcesPanel";

function setState(patch: Partial<AppState>) {
  useStore.setState({
    searchQuery: "epoxy zinc",
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

describe("SourcesPanel SureChEMBL links", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getSourceStatus").mockResolvedValue({
      surechembl: { available: true },
    } as never);
    setState({
      sources: [
        {
          source: "surechembl",
          identifier: "CN-104789083-B",
          title: "Quick-drying zinc phosphate epoxy primer",
          snippet: "doc CN-104789083-B",
          relevance: 0.9,
          url: "https://patents.google.com/patent/CN104789083B",
          url_alt: "https://www.surechembl.org/document/CN-104789083-B",
        },
      ] as never,
      selectedSources: ["CN-104789083-B"],
    });
  });

  it("shows Patents and SureChEMBL anchors for surechembl evidence", async () => {
    render(<SourcesPanel />);
    await waitFor(() => expect(api.getSourceStatus).toHaveBeenCalled());
    const patents = screen.getByRole("link", { name: "Patents" });
    expect(patents).toHaveAttribute("href", "https://patents.google.com/patent/CN104789083B");
    const sch = screen.getByRole("link", { name: "SureChEMBL" });
    expect(sch).toHaveAttribute("href", "https://www.surechembl.org/document/CN-104789083-B");
    expect(screen.getByText("CN-104789083-B")).toBeInTheDocument();
  });
});
