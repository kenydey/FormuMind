import { useState, useEffect, type RefObject } from "react";

// Shared chart utilities and dark industrial theme for FormuMind visualizations.
// All colors align with the existing Tailwind dark theme (ink/panel/edge/accent).

export const CHART_THEME = {
  bg: "transparent",
  axis: "#475569",
  grid: "#334155",
  text: "#94a3b8",
  textHighlight: "#e2e8f0",
  accent: "#38bdf8",
  accent2: "#a78bfa",
  pareto: "#fbbf24",
  measured: "#34d399",
  predicted: "#94a3b8",
  warning: "#f87171",
  contourLow: "#1e3a5f",
  contourMid: "#38bdf8",
  contourHigh: "#fbbf24",
  hover: "#38bdf8",
  selection: "rgba(56, 189, 248, 0.15)",
} as const;

export interface ChartDimensions {
  width: number;
  height: number;
  innerWidth: number;
  innerHeight: number;
  margin: { top: number; right: number; bottom: number; left: number };
}

export function useChartDimensions(
  margin: { top: number; right: number; bottom: number; left: number } = { top: 20, right: 20, bottom: 40, left: 50 }
): [RefObject<HTMLDivElement | null>, ChartDimensions] {
  const ref = { current: null as HTMLDivElement | null };
  const [dims, setDims] = useState<ChartDimensions>({
    width: 0, height: 0, innerWidth: 0, innerHeight: 0, margin,
  });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setDims({
          width,
          height,
          innerWidth: Math.max(0, width - margin.left - margin.right),
          innerHeight: Math.max(0, height - margin.top - margin.bottom),
          margin,
        });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [margin.top, margin.right, margin.bottom, margin.left]);

  return [ref, dims];
}

export function linearScale(
  domain: [number, number],
  range: [number, number]
): (value: number) => number {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const scale = (r1 - r0) / (d1 - d0 || 1);
  return (v: number) => r0 + (v - d0) * scale;
}

export function niceDomain(values: number[], pad: number = 0.05): [number, number] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  return [min - range * pad, max + range * pad];
}

export function formatNumber(n: number, digits: number = 1): string {
  if (Math.abs(n) >= 1000) return n.toFixed(0);
  if (Math.abs(n) >= 100) return n.toFixed(digits);
  if (Math.abs(n) >= 10) return n.toFixed(digits + 1);
  return n.toFixed(digits + 2);
}
