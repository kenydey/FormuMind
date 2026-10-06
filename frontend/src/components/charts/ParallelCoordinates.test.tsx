/**
 * The leaderboard charts draw a formulation from its *predicted* values only.
 *
 * ``Formulation.measured`` used to exist "so charts can prefer real measurements" - but nothing, in the backend or the
 * client, ever produced it: the field was a fallback for data that could not exist, and it kept a "实测" series in the
 * Pareto plot that could never contain a point. It is gone; this pins that a stray ``measured`` (an old saved payload,
 * a hand-edited project file) is simply ignored instead of silently changing what is drawn.
 */
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Formulation } from "../../api";
import ParallelCoordinates from "./ParallelCoordinates";

const AXES = [
  { key: "salt_spray_hours", label: "耐盐雾", direction: "maximize" as const },
  { key: "cost_cny_per_kg", label: "成本", direction: "minimize" as const },
];

function formulation(name: string, predicted: Record<string, number>): Formulation {
  return {
    name,
    domain: "anticorrosion_coating",
    ingredients: [],
    rationale: "",
    predicted,
    predicted_std: {},
    score: null,
    warnings: [],
  } as Formulation;
}

describe("ParallelCoordinates", () => {
  it("draws one line per formulation that has a prediction on every axis it is asked about", () => {
    const { container } = render(
      <ParallelCoordinates
        axes={AXES}
        formulations={[
          formulation("A", { salt_spray_hours: 700, cost_cny_per_kg: 24 }),
          formulation("B", { salt_spray_hours: 500, cost_cny_per_kg: 18 }),
          formulation("C", { salt_spray_hours: 900 }), // one axis only: not a line
        ]}
      />
    );
    expect(container.querySelectorAll("polyline")).toHaveLength(2);
  });

  it("ignores a stray `measured` on a formulation", () => {
    const stray = { ...formulation("D", { salt_spray_hours: 600 }), measured: { cost_cny_per_kg: 20 } };
    const { container } = render(<ParallelCoordinates axes={AXES} formulations={[stray as Formulation]} />);
    expect(container.querySelectorAll("polyline")).toHaveLength(0);
  });
});

describe("the Formulation type", () => {
  it("has no `measured` field", () => {
    // @ts-expect-error - `measured` is not a Formulation field; if it comes back this line stops being an error and tsc fails
    const bad: Formulation = { ...formulation("E", {}), measured: { x: 1 } };
    expect(bad.name).toBe("E");
  });
});
