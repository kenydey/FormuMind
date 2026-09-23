import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

export type KgFeedbackStatsShape = {
  measured_total: number;
  measured_performance: number;
  measured_material?: number;
  measured_domain?: number;
};

/**
 * Always-on strip for G5: measured feedback counts without requiring an
 * entity search (unlike KgRelationPanel which returns null when empty).
 */
export default function KgFeedbackStatsStrip({
  refreshKey = 0,
  compact = false,
}: {
  /** Bump after Workbench sync so counts refresh. */
  refreshKey?: number;
  compact?: boolean;
}) {
  const [stats, setStats] = useState<KgFeedbackStatsShape | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .kgFeedbackStats()
      .then((r) => {
        setStats({
          measured_total: r.measured_total,
          measured_performance: r.measured_performance,
          measured_material: r.measured_material,
          measured_domain: r.measured_domain,
        });
        setError(null);
      })
      .catch(() => {
        setStats(null);
        setError("stats_unavailable");
      });
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (error || !stats) return null;
  if (compact && stats.measured_total <= 0) return null;

  const mat =
    typeof stats.measured_material === "number" ? stats.measured_material : null;
  const dom =
    typeof stats.measured_domain === "number" ? stats.measured_domain : null;

  return (
    <p
      className={
        compact
          ? "text-[10px] text-slate-500"
          : "text-[10px] text-slate-400 px-2 py-1 border-b border-edge/30 bg-amber-500/5"
      }
      data-testid="kg-feedback-stats-strip"
      data-measured-total={stats.measured_total}
      data-measured-material={mat ?? undefined}
    >
      {stats.measured_total <= 0
        ? "实测反馈库：0 条（台账 Completed 同步后回流）"
        : `实测反馈库：${stats.measured_total} 条`}
      {stats.measured_total > 0 &&
        stats.measured_performance > 0 &&
        ` · 性能 ${stats.measured_performance}`}
      {stats.measured_total > 0 && mat != null && mat > 0 && ` · 材料级 ${mat}`}
      {stats.measured_total > 0 && dom != null && dom > 0 && ` · 领域级 ${dom}`}
    </p>
  );
}
