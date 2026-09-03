import { useMemo } from "react";

export interface ParetoPoint {
  id: string;
  x: number;
  y: number;
  is_pareto: boolean;
  source: "predicted" | "measured";
  [key: string]: unknown;
}

/**
 * Compute the Pareto front for a set of 2D points.
 * Supports both minimize and maximize directions per axis.
 * 
 * A point p is dominated by q if q is better or equal on both axes
 * and strictly better on at least one.
 */
export function computeParetoFront(
  points: Array<{ x: number; y: number }>,
  xDirection: "minimize" | "maximize" = "minimize",
  yDirection: "minimize" | "maximize" = "maximize"
): Set<number> {
  const better = (a: number, b: number, dir: "minimize" | "maximize") =>
    dir === "minimize" ? a <= b : a >= b;
  const strictlyBetter = (a: number, b: number, dir: "minimize" | "maximize") =>
    dir === "minimize" ? a < b : a > b;

  const indices = new Set<number>();
  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    let dominated = false;
    for (let j = 0; j < points.length; j++) {
      if (i === j) continue;
      const q = points[j];
      if (
        better(q.x, p.x, xDirection) &&
        better(q.y, p.y, yDirection) &&
        (strictlyBetter(q.x, p.x, xDirection) || strictlyBetter(q.y, p.y, yDirection))
      ) {
        dominated = true;
        break;
      }
    }
    if (!dominated) indices.add(i);
  }
  return indices;
}

export function useParetoFront<T extends { x: number; y: number }>(
  points: T[],
  xDirection: "minimize" | "maximize" = "minimize",
  yDirection: "minimize" | "maximize" = "minimize"
): (T & { is_pareto: boolean })[] {
  return useMemo(() => {
    const front = computeParetoFront(points, xDirection, yDirection);
    return points.map((p, i) => ({ ...p, is_pareto: front.has(i) }));
  }, [points, xDirection, yDirection]);
}
