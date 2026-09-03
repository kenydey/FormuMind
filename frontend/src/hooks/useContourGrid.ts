import { useMemo } from "react";

export interface GridPoint {
  x: number;
  y: number;
  z: number;
}

/**
 * Inverse Distance Weighting (IDW) interpolation.
 * O(n_grid * n_points) — fine for 40x40 grids.
 */
export function idwInterpolate(
  points: GridPoint[],
  gridX: number[],
  gridY: number[],
  power: number = 2
): number[][] {
  const ny = gridY.length;
  const nx = gridX.length;
  const grid: number[][] = Array.from({ length: ny }, () => Array(nx).fill(0));

  for (let yi = 0; yi < ny; yi++) {
    for (let xi = 0; xi < nx; xi++) {
      const gx = gridX[xi];
      const gy = gridY[yi];
      let wSum = 0;
      let zSum = 0;
      for (const p of points) {
        const d2 = (p.x - gx) ** 2 + (p.y - gy) ** 2;
        if (d2 === 0) {
          grid[yi][xi] = p.z;
          wSum = 0;
          break;
        }
        const w = 1 / Math.pow(d2, power / 2);
        wSum += w;
        zSum += w * p.z;
      }
      if (wSum > 0) grid[yi][xi] = zSum / wSum;
    }
  }
  return grid;
}

/**
 * Simplified marching squares for contour line generation.
 * Returns SVG path strings for each contour level.
 */
export function generateContourPaths(
  grid: number[][],
  gridX: number[],
  gridY: number[],
  levels: number[]
): Array<{ level: number; paths: string[] }> {
  const ny = grid.length;
  const nx = grid[0]?.length || 0;
  const result: Array<{ level: number; paths: string[] }> = [];

  for (const level of levels) {
    const segments: Array<[number, number, number, number]> = [];

    for (let yi = 0; yi < ny - 1; yi++) {
      for (let xi = 0; xi < nx - 1; xi++) {
        const z00 = grid[yi][xi];
        const z10 = grid[yi][xi + 1];
        const z01 = grid[yi + 1][xi];
        const z11 = grid[yi + 1][xi + 1];

        const x0 = gridX[xi];
        const x1 = gridX[xi + 1];
        const y0 = gridY[yi];
        const y1 = gridY[yi + 1];

        // Interpolate crossing points on edges
        const cross = (a: number, b: number, ya: number, yb: number): number | null => {
          if ((a - level) * (b - level) >= 0) return null;
          return ya + (yb - ya) * (level - a) / (b - a);
        };

        const left = cross(z00, z01, y0, y1);
        const right = cross(z10, z11, y0, y1);
        const top = cross(z00, z10, x0, x1);
        const bottom = cross(z01, z11, x0, x1);

        // Simple case analysis: connect valid crossing pairs
        const pts: Array<[number, number]> = [];
        if (top != null) pts.push([top, y0]);
        if (right != null) pts.push([x1, right]);
        if (bottom != null) pts.push([bottom, y1]);
        if (left != null) pts.push([x0, left]);

        if (pts.length >= 2) {
          segments.push([pts[0][0], pts[0][1], pts[1][0], pts[1][1]]);
        }
        if (pts.length === 4) {
          segments.push([pts[2][0], pts[2][1], pts[3][0], pts[3][1]]);
        }
      }
    }

    // Convert segments to SVG path strings
    const paths: string[] = [];
    for (const [x1, y1, x2, y2] of segments) {
      paths.push(`M ${x1} ${y1} L ${x2} ${y2}`);
    }
    result.push({ level, paths });
  }

  return result;
}

export function useContourGrid(
  points: GridPoint[],
  xDomain: [number, number],
  yDomain: [number, number],
  gridSize: number = 40,
  levels: number = 10
) {
  return useMemo(() => {
    if (points.length < 3) return { grid: [], gridX: [], gridY: [], paths: [], levelValues: [] };

    const [xMin, xMax] = xDomain;
    const [yMin, yMax] = yDomain;
    const dx = (xMax - xMin) / (gridSize - 1);
    const dy = (yMax - yMin) / (gridSize - 1);

    const gridX = Array.from({ length: gridSize }, (_, i) => xMin + i * dx);
    const gridY = Array.from({ length: gridSize }, (_, i) => yMin + i * dy);

    const grid = idwInterpolate(points, gridX, gridY, 2);

    const allZ = points.map((p) => p.z);
    const zMin = Math.min(...allZ);
    const zMax = Math.max(...allZ);
    const levelValues = Array.from({ length: levels }, (_, i) =>
      zMin + (i / (levels - 1)) * (zMax - zMin)
    );

    const paths = generateContourPaths(grid, gridX, gridY, levelValues);

    return { grid, gridX, gridY, paths, levelValues };
  }, [points, xDomain[0], xDomain[1], yDomain[0], yDomain[1], gridSize, levels]);
}
