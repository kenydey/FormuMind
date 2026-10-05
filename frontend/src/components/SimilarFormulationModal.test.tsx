import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type Formulation } from "../api";
import SimilarFormulationModal, { similarityFactors } from "./SimilarFormulationModal";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: { ...actual.api, kgSimilarFormulations: vi.fn() } };
});

// What a recommendation really carries: ingredients, no `factors` (the backend has no such field).
const recommended = {
  name: "Epoxy primer",
  domain: "anticorrosion_coating",
  ingredients: [
    { name: "Bisphenol-A epoxy (DGEBA)", role: "resin", weight_pct: 38 },
    { name: "Zinc phosphate", role: "inhibitor", weight_pct: 6.5 },
    { name: "Xylene", role: "solvent", weight_pct: 0 },
  ],
  rationale: "",
  predicted: {},
  predicted_std: {},
  score: 0.7,
  warnings: [],
} as unknown as Formulation;

describe("similar historical formulations", () => {
  beforeEach(() => {
    vi.mocked(api.kgSimilarFormulations).mockReset();
    vi.mocked(api.kgSimilarFormulations).mockResolvedValue({ matches: [] } as never);
  });

  it("queries with the recipe's own ingredient weights when the formulation has no factors", async () => {
    render(<SimilarFormulationModal formulation={recommended} onClose={() => {}} />);
    await waitFor(() => expect(api.kgSimilarFormulations).toHaveBeenCalled());
    expect(api.kgSimilarFormulations).toHaveBeenCalledWith({ "Bisphenol-A epoxy (DGEBA)": 38, "Zinc phosphate": 6.5 }, 10);
  });

  it("shows the matches it gets back", async () => {
    vi.mocked(api.kgSimilarFormulations).mockResolvedValue({
      matches: [
        { experiment_id: 7, project_id: "p1", project_title: "旧项目", similarity: 0.91, factors: { "Zinc phosphate": 6 }, measured: { salt_spray_hours: 800 }, shared_ingredients: ["Zinc phosphate"], differing_ingredients: [] },
      ],
    } as never);
    render(<SimilarFormulationModal formulation={recommended} onClose={() => {}} />);
    expect(await screen.findByText(/91\.0%/)).toBeTruthy();
  });

  it("an explicit measured-run factors map still wins", () => {
    expect(similarityFactors({ ...recommended, factors: { "Zinc phosphate": 5, note: "x" as never } })).toEqual({ "Zinc phosphate": 5 });
  });

  it("a formulation with nothing to compare does not call the backend", async () => {
    render(<SimilarFormulationModal formulation={{ ...recommended, ingredients: [] }} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("未找到相似历史配方")).toBeTruthy());
    expect(api.kgSimilarFormulations).not.toHaveBeenCalled();
  });
});
