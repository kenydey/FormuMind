import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import FormulaExplainPanel from "./FormulaExplainPanel";

/**
 * Regression: ErrorBoundary「Cannot read properties of undefined (reading 'slice')」
 * — legacy leaderboard explain shapes hydrated from zustand/localStorage persisted by
 * older builds must render without throwing.
 */
describe("FormulaExplainPanel legacy payload tolerance", () => {
  it("renders legacy string evidence_refs without throwing", () => {
    render(
      <FormulaExplainPanel
        explain={
          {
            evidence_refs: ["src-abc", "doi:10.1/x"],
          } as never
        }
        score={0.81}
      />,
    );
    expect(screen.getByTestId("formula-explain-panel")).toBeTruthy();
    // string entry degrades to 「ref:src-abc」 chip instead of crashing
    expect(screen.getByTestId("explain-evidence").textContent).toContain(
      "ref:src-abc",
    );
  });

  it("renders refs missing source_id/source_type keys without throwing", () => {
    render(
      <FormulaExplainPanel
        explain={
          {
            evidence_refs: [{ source_type: "patent" }, {}],
          } as never
        }
      />,
    );
    expect(screen.getByTestId("explain-evidence").textContent).toContain(
      "ref:",
    );
  });

  it("renders explain where kg_signals arrays are null (not just absent)", () => {
    render(
      <FormulaExplainPanel
        explain={
          {
            kg_signals: {
              feasible: true,
              measured_materials: null,
              synergizes: null,
              inhibits: null,
            },
          } as never
        }
      />,
    );
    expect(screen.getByTestId("explain-kg")).toBeTruthy();
  });
});
