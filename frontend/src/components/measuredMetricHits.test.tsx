import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import MeasuredMetricHitsBanner from "./MeasuredMetricHitsBanner";
import {
  bestMeasuredQuality,
  summarizeMeasuredMetricHits,
  type MeasuredMetricHit,
} from "./measuredMetricHits";

const hits = (rows: Partial<MeasuredMetricHit>[]): MeasuredMetricHit[] =>
  rows.map((r) => ({
    material: r.material ?? "Zinc phosphate",
    metric: r.metric ?? "salt_spray_hours",
    quality: r.quality ?? "presence",
    value: r.value,
  }));

describe("summarizeMeasuredMetricHits", () => {
  it("returns null for empty hits", () => {
    expect(summarizeMeasuredMetricHits([])).toBeNull();
    expect(summarizeMeasuredMetricHits(undefined)).toBeNull();
  });

  it("prefers good over poor when both present", () => {
    expect(
      bestMeasuredQuality(
        hits([
          { quality: "poor", material: "Weak" },
          { quality: "good", material: "Zinc phosphate" },
        ]),
      ),
    ).toBe("good");
  });

  it("labels good hits with materials and metrics", () => {
    const s = summarizeMeasuredMetricHits(
      hits([{ quality: "good", material: "Zinc phosphate", metric: "salt_spray_hours" }]),
    );
    expect(s?.quality).toBe("good");
    expect(s?.label).toContain("目标指标实测加成");
    expect(s?.label).toContain("Zinc phosphate");
    expect(s?.label).toContain("salt_spray_hours");
  });

  it("labels poor hits as demotion", () => {
    const s = summarizeMeasuredMetricHits(
      hits([{ quality: "poor", material: "Weak pigment", metric: "salt_spray_hours" }]),
    );
    expect(s?.quality).toBe("poor");
    expect(s?.label).toContain("偏弱降权");
  });
});

describe("MeasuredMetricHitsBanner", () => {
  it("renders nothing without hits", () => {
    const { container } = render(<MeasuredMetricHitsBanner hits={[]} />);
    expect(container.querySelector("[data-testid=measured-metric-hits]")).toBeNull();
  });

  it("renders good quality with testid", () => {
    render(
      <MeasuredMetricHitsBanner
        hits={hits([{ quality: "good", material: "Zinc phosphate", metric: "salt_spray_hours" }])}
      />,
    );
    const el = screen.getByTestId("measured-metric-hits");
    expect(el).toHaveAttribute("data-quality", "good");
    expect(el.textContent).toContain("目标指标实测加成");
    expect(el.textContent).toContain("Zinc phosphate");
  });

  it("renders poor quality distinctly", () => {
    render(
      <MeasuredMetricHitsBanner
        hits={hits([{ quality: "poor", material: "Weak pigment", metric: "salt_spray_hours" }])}
      />,
    );
    const el = screen.getByTestId("measured-metric-hits");
    expect(el).toHaveAttribute("data-quality", "poor");
    expect(el.textContent).toContain("偏弱降权");
  });
});
