/**
 * EmbodimentDraftModal — confirm gate never claims production pool write.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api, type EmbodimentDraft } from "../api";
import EmbodimentDraftModal from "./EmbodimentDraftModal";

const DRAFT: EmbodimentDraft = {
  status: "draft",
  needs_review: true,
  origin: "patent_fulltext",
  source_id: "src-1",
  doc_id: "CN104789083B",
  title: "Primer",
  amount_source: "table",
  embodiments: [
    {
      label: "Example 1",
      ingredients: [
        { name: "Epoxy resin", role: "additive", weight_pct: 55 },
        { name: "Zinc phosphate", role: "additive", weight_pct: 45 },
      ],
      amount_source: "table",
    },
  ],
  formulation: {
    name: "草稿",
    domain: "anticorrosion_coating",
    ingredients: [
      { name: "Epoxy resin", role: "additive", weight_pct: 55 },
      { name: "Zinc phosphate", role: "additive", weight_pct: 45 },
    ],
    warnings: ["表格比重"],
    source: "patent_fulltext",
  },
};

describe("EmbodimentDraftModal", () => {
  it("confirms via formulations API with promoted_to_pool=false", async () => {
    const confirm = vi.spyOn(api, "confirmEmbodimentDraft").mockResolvedValue({
      ok: true,
      source_id: "src-1",
      doc_id: "CN104789083B",
      pending_materials: [{ action: "pending", name: "Epoxy resin" }],
      formulation_entity_id: "form:fulltext:abc:embodiment",
      promoted_to_pool: false,
      note: "草稿已确认：原料进入 pending",
    });
    const onConfirmed = vi.fn();
    render(<EmbodimentDraftModal draft={DRAFT} onClose={() => {}} onConfirmed={onConfirmed} />);
    expect(screen.getByTestId("embodiment-amount-source").textContent).toBe("table");
    fireEvent.click(screen.getByRole("button", { name: "人工确认入库" }));
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId("embodiment-draft-confirmed")).toBeInTheDocument());
    expect(onConfirmed).toHaveBeenCalled();
  });

  it("uses surechembl confirm when origin=surechembl", async () => {
    const sch = vi.spyOn(api, "surechemblConfirmExampleDraft").mockResolvedValue({
      ok: true,
      doc_id: "CN-1",
      pending_materials: [],
      formulation_entity_id: "form:x",
      promoted_to_pool: false,
      note: "ok",
    });
    const draft: EmbodimentDraft = {
      ...DRAFT,
      origin: "surechembl",
      amount_source: "placeholder",
      embodiments: undefined,
    };
    render(<EmbodimentDraftModal draft={draft} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "人工确认入库" }));
    await waitFor(() => expect(sch).toHaveBeenCalled());
  });

  it("hides confirm when ingredients are empty (F0)", () => {
    const draft: EmbodimentDraft = {
      ...DRAFT,
      amount_source: "placeholder",
      embodiments: [
        {
          label: "No formulation table",
          ingredients: [],
          amount_source: "placeholder",
        },
      ],
      formulation: {
        name: "空草稿",
        domain: "anticorrosion_coating",
        ingredients: [],
        warnings: ["未识别到可用配方表"],
        source: "patent_fulltext",
      },
    };
    render(<EmbodimentDraftModal draft={draft} onClose={() => {}} />);
    expect(screen.getByTestId("embodiment-empty-ingredients")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "人工确认入库" })).not.toBeInTheDocument();
  });
});
