/**
 * SourcesPanel SureChEMBL P3 — KG ingest + extract draft actions.
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

const DOC = {
  source: "surechembl",
  identifier: "CN-104789083-B",
  title: "Quick-drying zinc phosphate epoxy primer",
  snippet: "doc CN-104789083-B · assignee ACME",
  relevance: 0.9,
  url: "https://patents.google.com/patent/CN104789083B",
  url_alt: "https://www.surechembl.org/document/CN-104789083-B",
  assignee: "ACME",
  pub_date: "20170725",
};

describe("SourcesPanel SureChEMBL P3 actions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getSourceStatus").mockResolvedValue({
      surechembl: { available: true },
    } as never);
    vi.spyOn(api, "kbSources").mockResolvedValue({ sources: [] } as never);
    vi.spyOn(api, "embodimentEligibility").mockResolvedValue({ items: [] });
    setState({
      sources: [DOC] as never,
      selectedSources: ["CN-104789083-B"],
    });
  });

  it("shows 入库图谱 and 提取实施例草稿 for surechembl rows", async () => {
    render(<SourcesPanel />);
    await waitFor(() => expect(api.getSourceStatus).toHaveBeenCalled());
    expect(screen.getByTestId("surechembl-ingest-kg-CN-104789083-B")).toBeInTheDocument();
    expect(screen.getByTestId("surechembl-extract-draft-CN-104789083-B")).toBeInTheDocument();
  });

  it("ingests KG on click", async () => {
    const ingest = vi.spyOn(api, "surechemblIngestDocument").mockResolvedValue({
      ok: true,
      doc_id: "CN-104789083-B",
      patent_entity_id: "patent:scpn:CN-104789083-B",
      entities: 3,
      links: 2,
      link_type: "appears_in",
      chemicals: 2,
    });
    render(<SourcesPanel />);
    fireEvent.click(screen.getByTestId("surechembl-ingest-kg-CN-104789083-B"));
    await waitFor(() => expect(ingest).toHaveBeenCalled());
    expect(ingest.mock.calls[0][0]).toMatchObject({
      doc_id: "CN-104789083-B",
      title: DOC.title,
      assignee: "ACME",
    });
    await waitFor(() =>
      expect(screen.getByTestId("surechembl-action-msg").textContent).toMatch(/图谱已更新/)
    );
  });

  it("opens draft review modal after extract", async () => {
    vi.spyOn(api, "surechemblExtractExampleDraft").mockResolvedValue({
      ok: true,
      draft: {
        status: "draft",
        needs_review: true,
        origin: "surechembl",
        doc_id: "CN-104789083-B",
        title: DOC.title,
        formulation: {
          name: "SureChEMBL 草稿 · CN-104789083-B",
          domain: "anticorrosion_coating",
          ingredients: [
            { name: "zinc phosphate", role: "additive", weight_pct: 100 },
          ],
          warnings: ["人审草稿"],
          source: "surechembl",
        },
      },
    });
    render(<SourcesPanel />);
    fireEvent.click(screen.getByTestId("surechembl-extract-draft-CN-104789083-B"));
    await waitFor(() => expect(screen.getByTestId("embodiment-draft-review")).toBeInTheDocument());
    expect(screen.getByText("zinc phosphate")).toBeInTheDocument();
    expect(screen.getByText(/不会/)).toBeInTheDocument();
  });
});
