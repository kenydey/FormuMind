/**
 * lookupCas fires an async /chemical/profile call keyed by array index. If
 * the leaderboard is replaced (new search/optimize/loop run) while that call
 * is still in flight, the stale result must not be written into whatever
 * ingredient now happens to sit at the same index in the *new* formulation.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ChemicalProfile, Ingredient } from "../api";
import RecommendedFormulaTable from "./RecommendedFormulaTable";

function ingredient(name: string, cas: string): Ingredient {
  return { name, role: "resin", weight_pct: 10, cas_no: cas };
}

function profile(query: string): ChemicalProfile {
  return {
    query,
    cas: "9999-99-9",
    iupac_name: `${query} (resolved)`,
    zh_name: "",
    formula: "",
    found: true,
    source: "pubchem",
    func_groups: [],
    patented: null,
    safety: { controlled: null, explosive: null },
    chemtools: { enabled: false, chemcrow_installed: false },
  };
}

describe("RecommendedFormulaTable stale CAS lookup", () => {
  it("does not write a resolved lookup into the ingredient that replaced the one queried", async () => {
    let resolveLookup!: (p: ChemicalProfile) => void;
    vi.spyOn(api, "chemicalProfile").mockReturnValue(
      new Promise<ChemicalProfile>((resolve) => {
        resolveLookup = resolve;
      })
    );

    const onChangeA = vi.fn();
    const formulaA = [ingredient("Bisphenol A", "")];
    const { rerender } = render(
      <RecommendedFormulaTable ingredients={formulaA} editable onIngredientChange={onChangeA} />
    );

    fireEvent.blur(screen.getByPlaceholderText("CAS"), { target: { value: "Bisphenol A" } });
    expect(api.chemicalProfile).toHaveBeenCalledWith("Bisphenol A");

    // Leaderboard replaced by a new run before the lookup resolves: a new
    // ingredients array and a fresh onIngredientChange for the new formula.
    const onChangeB = vi.fn();
    const formulaB = [ingredient("Titanium dioxide", "13463-67-7")];
    rerender(<RecommendedFormulaTable ingredients={formulaB} editable onIngredientChange={onChangeB} />);

    resolveLookup(profile("Bisphenol A"));
    await Promise.resolve();
    await Promise.resolve();

    expect(onChangeA).not.toHaveBeenCalled();
    expect(onChangeB).not.toHaveBeenCalled();
  });
});
