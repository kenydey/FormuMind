import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AdaptiveDoeInsights } from "./AdaptiveDoeInsights";

/**
 * Regression: meta.anomalies / meta.run_explanations undefined (payload persisted by
 * an older build, missing keys) previously crashed with
 * 「Cannot read properties of undefined (reading 'slice')」.
 */
describe("AdaptiveDoeInsights payload tolerance", () => {
  it("renders meta with missing anomalies/run_explanations keys", () => {
    render(
      <AdaptiveDoeInsights
        meta={{
          strategy_label: "balanced",
          strategy_rationale: "探索 + 利用",
        } as never}
      />,
    );
    expect(screen.getAllByText(/探索 \+ 利用/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/异常实验点/)).toBeNull();
  });

  it("renders when doePlan.runs is missing", () => {
    render(
      <AdaptiveDoeInsights
        meta={
          {
            strategy_rationale: "理由",
            run_explanations: [
              { run_id: 1, summary: "选点理由" },
            ],
          } as never
        }
        doePlan={{} as never}
      />,
    );
    // runs[] missing → no run rows render (expected), but no crash either
    expect(screen.queryByText(/异常实验点/)).toBeNull();
    expect(document.body).toBeTruthy();
  });

  it("renders anomalies slice safely", () => {
    render(
      <AdaptiveDoeInsights
        meta={
          {
            strategy_rationale: "r",
            anomalies: [
              { experiment_id: 1, type: "outlier", note: "偏差过大" },
            ],
          } as never
        }
      />,
    );
    expect(screen.getByText(/异常实验点/)).toBeTruthy();
  });
});
