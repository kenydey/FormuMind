/** Pure helpers for G5/G6 KG measured observability (Workbench tip + leaderboard chip). */

import {
  summarizeMeasuredMetricHits,
  type MeasuredMetricHit,
} from "./measuredMetricHits";

/** Workbench sync save-hint fragment when KG feedback wrote edges. */
export function formatKgWrittenHint(
  kgWritten: number | null | undefined,
): string | null {
  if (typeof kgWritten !== "number" || !Number.isFinite(kgWritten) || kgWritten <= 0) {
    return null;
  }
  return `KG 回流 ${kgWritten} 条实测证据`;
}

export type CardMeasuredChip = {
  label: string;
  quality: "good" | "poor" | "presence" | "binary";
  className: string;
  title: string;
};

const CHIP_STYLE: Record<"good" | "poor" | "presence" | "binary", string> = {
  good: "border-emerald-500/50 bg-emerald-500/10 text-emerald-400",
  presence: "border-sky-500/50 bg-sky-500/10 text-sky-300",
  poor: "border-amber-500/50 bg-amber-500/10 text-amber-400",
  binary: "border-emerald-500/50 bg-emerald-500/10 text-emerald-400",
};

/**
 * Compact header chip for FormulaLeaderboard cards (visible when collapsed).
 * Prefers metric-aware hits; falls back to binary measured_materials.
 */
export function cardMeasuredChip(
  hits: MeasuredMetricHit[] | null | undefined,
  measuredMaterials: string[] | null | undefined,
): CardMeasuredChip | null {
  const summary = summarizeMeasuredMetricHits(hits);
  if (summary) {
    const q = summary.quality;
    const short =
      q === "good" ? "实测加成" : q === "poor" ? "实测偏弱" : "实测存在";
    return {
      label: short,
      quality: q,
      className: CHIP_STYLE[q],
      title: summary.label,
    };
  }
  if (measuredMaterials && measuredMaterials.length > 0) {
    return {
      label: "实测",
      quality: "binary",
      className: CHIP_STYLE.binary,
      title: `实测验证：${measuredMaterials.join("、")}`,
    };
  }
  return null;
}
