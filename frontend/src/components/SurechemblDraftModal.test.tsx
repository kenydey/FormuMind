/**
 * SurechemblDraftModal — re-exports EmbodimentDraftModal; keep legacy test coverage.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api, type SurechemblExampleDraft } from "../api";
import SurechemblDraftModal from "./SurechemblDraftModal";

const DRAFT: SurechemblExampleDraft = {
  status: "draft",
  needs_review: true,
  origin: "surechembl",
  doc_id: "CN-104789083-B",
  title: "Primer",
  amount_source: "placeholder",
  formulation: {
    name: "草稿",
    domain: "anticorrosion_coating",
    ingredients: [{ name: "zinc phosphate", role: "additive", weight_pct: 100 }],
    warnings: ["占位"],
    source: "surechembl",
  },
};

describe("SurechemblDraftModal (alias)", () => {
  it("confirms with promoted_to_pool=false messaging", async () => {
    const confirm = vi.spyOn(api, "surechemblConfirmExampleDraft").mockResolvedValue({
      ok: true,
      doc_id: "CN-104789083-B",
      pending_materials: [{ action: "pending", name: "zinc phosphate" }],
      formulation_entity_id: "form:surechembl:abc:embodiment",
      promoted_to_pool: false,
      note: "草稿已确认：原料进入 pending",
    });
    const onConfirmed = vi.fn();
    render(<SurechemblDraftModal draft={DRAFT} onClose={() => {}} onConfirmed={onConfirmed} />);
    fireEvent.click(screen.getByRole("button", { name: "人工确认入库" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(DRAFT));
    await waitFor(() => expect(screen.getByTestId("embodiment-draft-confirmed")).toBeInTheDocument());
    expect(onConfirmed).toHaveBeenCalled();
  });
});
