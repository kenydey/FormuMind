import { describe, expect, it } from "vitest";
import { computeParetoFront } from "./useParetoFront";

// x = cost (lower is better), y = salt-spray hours (higher is better): the default directions of the leaderboard plot.
const pts = (...xy: [number, number][]) => xy.map(([x, y]) => ({ x, y }));

describe("computeParetoFront", () => {
  it("keeps exactly the points no other point beats on both axes", () => {
    const front = computeParetoFront(pts([10, 500], [12, 700], [15, 650], [9, 400], [20, 900]), "minimize", "maximize");
    // (15, 650) is beaten by (12, 700); the other four each win on at least one axis
    expect([...front].sort()).toEqual([0, 1, 3, 4]);
  });

  it("honours the direction of each axis", () => {
    const points = pts([1, 1], [2, 2], [3, 3]);
    expect([...computeParetoFront(points, "minimize", "minimize")]).toEqual([0]);
    expect([...computeParetoFront(points, "maximize", "maximize")]).toEqual([2]);
    expect([...computeParetoFront(points, "minimize", "maximize")].sort()).toEqual([0, 1, 2]);
  });

  it("treats identical points as all on the front (neither strictly beats the other)", () => {
    expect([...computeParetoFront(pts([5, 5], [5, 5]), "minimize", "maximize")].sort()).toEqual([0, 1]);
  });

  it("handles no points and one point", () => {
    expect(computeParetoFront([]).size).toBe(0);
    expect([...computeParetoFront(pts([1, 2]))]).toEqual([0]);
  });
});
