/**
 * SourcesPanel P3.2 — Evidence 「入库全文」 + source_id alias resolve.
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

describe("SourcesPanel P3.2 ingest fulltext", () => {
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

  it("shows 入库全文 beside 入库图谱", async () => {
    render(<SourcesPanel />);
    await waitFor(() => expect(api.getSourceStatus).toHaveBeenCalled());
    expect(screen.getByTestId("ingest-fulltext-CN-104789083-B")).toBeInTheDocument();
    expect(screen.getByTestId("surechembl-ingest-kg-CN-104789083-B")).toBeInTheDocument();
  });

  it("ingests fulltext and shows 已入库 badge", async () => {
    const ingest = vi.spyOn(api, "ingestEvidence").mockResolvedValue({
      ok: true,
      status: "indexed",
      source_id: "src-abc",
      canonical_id: "CN104789083B",
      reason: null,
      kind: "patent",
    });
    vi.spyOn(api, "kbSources").mockResolvedValue({
      sources: [
        {
          id: "src-abc",
          title: DOC.title,
          filename: "CN104789083B",
          source_kind: "patent",
          origin_url: "CN104789083B",
          raw_text_chars: 12000,
          extraction_status: "fulltext",
        },
      ],
    } as never);
    vi.spyOn(api, "embodimentEligibility").mockResolvedValue({
      items: [{ source_id: "src-abc", eligible: true }],
    });

    render(<SourcesPanel />);
    fireEvent.click(screen.getByTestId("ingest-fulltext-CN-104789083-B"));
    await waitFor(() => expect(ingest).toHaveBeenCalled());
    expect(ingest.mock.calls[0][0]).toMatchObject({
      identifier: "CN-104789083-B",
      source: "surechembl",
    });
    await waitFor(() =>
      expect(screen.getByTestId("surechembl-action-msg").textContent).toMatch(/全文已入库/)
    );
    expect(screen.getByText("已入库")).toBeInTheDocument();
  });

  it("maps compact origin_url to hyphenated evidence for fulltext extract", async () => {
    vi.spyOn(api, "kbSources").mockResolvedValue({
      sources: [
        {
          id: "src-compact",
          title: DOC.title,
          filename: "CN104789083B",
          source_kind: "patent",
          origin_url: "CN104789083B",
          raw_text_chars: 12000,
          extraction_status: "fulltext",
        },
      ],
    } as never);
    vi.spyOn(api, "embodimentEligibility").mockResolvedValue({
      items: [{ source_id: "src-compact", eligible: true }],
    });
    const extract = vi.spyOn(api, "extractEmbodimentDraft").mockResolvedValue({
      ok: true,
      draft: {
        status: "draft",
        needs_review: true,
        origin: "surechembl+fulltext",
        source_id: "src-compact",
        title: DOC.title,
        amount_source: "table",
        formulation: {
          name: "实施例草稿",
          domain: "anticorrosion_coating",
          ingredients: [{ name: "树脂", role: "resin", weight_pct: 70 }],
          warnings: [],
          source: "patent_fulltext",
        },
      },
    } as never);
    const placeholder = vi.spyOn(api, "surechemblExtractExampleDraft");

    render(<SourcesPanel />);
    await waitFor(() => expect(api.kbSources).toHaveBeenCalled());
    await waitFor(() => expect(api.embodimentEligibility).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("surechembl-extract-draft-CN-104789083-B"));
    await waitFor(() => expect(extract).toHaveBeenCalled());
    expect(extract.mock.calls[0][0]).toMatchObject({
      source_id: "src-compact",
      surechembl_hint: true,
    });
    expect(placeholder).not.toHaveBeenCalled();
  });
});
