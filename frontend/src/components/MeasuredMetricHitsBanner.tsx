import { summarizeMeasuredMetricHits, type MeasuredMetricHit } from "./measuredMetricHits";

/** Compact banner for FormulaLeaderboard expand panel. */
export default function MeasuredMetricHitsBanner({
  hits,
}: {
  hits: MeasuredMetricHit[] | null | undefined;
}) {
  const summary = summarizeMeasuredMetricHits(hits);
  if (!summary) return null;
  return (
    <div
      data-testid="measured-metric-hits"
      data-quality={summary.quality}
      className={`text-[10px] rounded px-1.5 py-0.5 ${summary.className}`}
      title={summary.metrics.length ? `指标：${summary.metrics.join(", ")}` : undefined}
    >
      {summary.quality === "good" ? "✓ " : summary.quality === "poor" ? "↓ " : "· "}
      {summary.label}
    </div>
  );
}
