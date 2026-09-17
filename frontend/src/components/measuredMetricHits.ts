/** Metric-aware measured hits banner for recommend leaderboard cards. */

export type MeasuredMetricHit = {
  material: string;
  metric: string;
  quality: "good" | "poor" | "presence" | string;
  value?: number | null;
  confidence?: number;
  prop_id?: string;
};

export type MeasuredMetricHitsSummary = {
  quality: "good" | "poor" | "presence";
  materials: string[];
  metrics: string[];
  label: string;
  className: string;
};

const QUALITY_RANK: Record<string, number> = {
  good: 3,
  presence: 2,
  poor: 1,
};

const QUALITY_STYLE: Record<"good" | "poor" | "presence", { className: string; verb: string }> = {
  good: {
    className: "text-emerald-400 border border-emerald-500/30 bg-emerald-500/10",
    verb: "目标指标实测加成",
  },
  presence: {
    className: "text-sky-300 border border-sky-500/30 bg-sky-500/10",
    verb: "目标指标实测存在",
  },
  poor: {
    className: "text-amber-400 border border-amber-500/30 bg-amber-500/10",
    verb: "目标指标实测偏弱降权",
  },
};

function normalizeQuality(raw: string | undefined): "good" | "poor" | "presence" {
  if (raw === "good" || raw === "poor" || raw === "presence") return raw;
  return "presence";
}

/** Pick the strongest quality among hits (good > presence > poor). */
export function bestMeasuredQuality(
  hits: MeasuredMetricHit[] | null | undefined,
): "good" | "poor" | "presence" | null {
  if (!hits?.length) return null;
  let best: "good" | "poor" | "presence" | null = null;
  let bestRank = 0;
  for (const h of hits) {
    const q = normalizeQuality(h.quality);
    const rank = QUALITY_RANK[q] ?? 0;
    if (rank > bestRank) {
      bestRank = rank;
      best = q;
    }
  }
  return best;
}

/** Build a single banner summary for a formulation card; null when no hits. */
export function summarizeMeasuredMetricHits(
  hits: MeasuredMetricHit[] | null | undefined,
): MeasuredMetricHitsSummary | null {
  const quality = bestMeasuredQuality(hits);
  if (!quality || !hits?.length) return null;

  const materials = [...new Set(hits.map((h) => h.material).filter(Boolean))].sort();
  const metrics = [...new Set(hits.map((h) => h.metric).filter(Boolean))].sort();
  const style = QUALITY_STYLE[quality];
  const matPart = materials.join("、") || "材料";
  const metricPart = metrics.join("/") || "指标";
  return {
    quality,
    materials,
    metrics,
    label: `${style.verb}：${matPart}（${metricPart}）`,
    className: style.className,
  };
}
