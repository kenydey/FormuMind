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
