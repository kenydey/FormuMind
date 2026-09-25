import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import FormulaExplainPanel from "./FormulaExplainPanel";

describe("FormulaExplainPanel", () => {
  it("renders structured explain sections", () => {
    render(
      <FormulaExplainPanel
        score={0.82}
        explain={{
          objectives_hit: ["salt_spray_hours=900"],
          constraints_miss: ["adhesion: 无预测值"],
          evidence_refs: [{ source_type: "patent", source_id: "US123" }],
          kg_signals: {
            feasible: true,
            measured_materials: ["Zinc phosphate"],
            synergizes: ["Epoxy↔Amine"],
            inhibits: [],
          },
          supply_flags: ["供应风险：交期长"],
          uncertainty: ["salt_spray_hours 不确定性偏高 (±250)"],
          bias_corrected: true,
          bias_corrected_metrics: ["salt_spray_hours"],
          notes: ["barrier"],
          effect_trace: [
            {
              field: "objectives.salt_spray_hours",
              kind: "objective",
              label: "salt_spray_hours",
              status: "wired",
              consumers: ["recommend"],
            },
          ],
        }}
      />,
    );
    expect(screen.getByTestId("formula-explain-panel")).toBeTruthy();
    expect(screen.getByTestId("formula-explain-bias")).toHaveTextContent("已偏差校准");
    expect(screen.getByTestId("explain-hits")).toHaveTextContent("salt_spray_hours");
    expect(screen.getByTestId("explain-misses")).toHaveTextContent("adhesion");
    expect(screen.getByTestId("explain-evidence")).toHaveTextContent("patent:US123");
    expect(screen.getByTestId("explain-supply")).toHaveTextContent("供应风险");
    expect(screen.getByTestId("explain-effect-trace")).toHaveTextContent("生效");
  });

  it("shows score-only fallback", () => {
    render(<FormulaExplainPanel score={0.5} />);
    expect(screen.getByTestId("explain-score-only")).toBeTruthy();
  });
});
