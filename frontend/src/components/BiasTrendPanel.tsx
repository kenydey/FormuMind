import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import BiasTrendChart from "./charts/BiasTrendChart";

type BiasTrend = {
  campaign_id: number;
  trend: { at: string | null; n_rows: number; by_metric: Record<string, { n: number; mean_error: number; rmse: number; mae: number; max_abs: number }> }[];
  alerts: string[];
};

export default function BiasTrendPanel({ campaignId }: { campaignId: number | null }) {
  const [data, setData] = useState<BiasTrend | null>(null);
  const rmseHistory = useStore((s) => s.rmseHistory);

  useEffect(() => {
    if (!campaignId) return;
    api
      .getBiasTrend(campaignId)
      .then(setData)
      .catch(() => {});
  }, [campaignId]);

  if (!campaignId || !data || data.trend.length === 0) return null;

  const slice = data.trend.slice(-8);
  const metrics = Array.from(new Set(slice.flatMap((t) => Object.keys(t.by_metric))));
  const primary = metrics[0];
  const values = slice.map((t) => t.by_metric[primary]?.rmse ?? 0);
  const maeValues = slice.map((t) => t.by_metric[primary]?.mae ?? 0);
  // 模型 RMSE 历史（store.rmseHistory 同指标，取近 8 轮）
  const modelSlice = rmseHistory.slice(-8);
  const modelValues = modelSlice.map((h) => (h[primary] ?? 0));
  const maxV = Math.max(1, ...values, ...maeValues, ...modelValues, 50);
  const w = 300, h = 60, pad = 6;
  const stepBias = slice.length > 1 ? (w - pad * 2) / (slice.length - 1) : 0;
  const stepModel = modelSlice.length > 1 ? (w - pad * 2) / (modelSlice.length - 1) : 0;
  const y = (v: number) => h - pad - (v / maxV) * (h - pad * 2);
  const path = (vals: number[], step: number) => vals.map((v, i) => `${i === 0 ? "M" : "L"} ${pad + i * step} ${y(v)}`).join(" ");
  const thrY = y(50);

  return (
    <div className="rounded border border-edge/40 bg-ink/40 px-2 py-1.5 text-[11px]">
      <div className="flex items-center justify-between mb-1">
        <span className="text-amber-300 font-medium">预测偏差趋势（{data.trend.length} 次同步）· {primary}</span>
        <span className="text-slate-500 text-[10px]">阈值 RMSE {data.alerts.length > 0 ? "⚠" : ""}</span>
      </div>
      <BiasTrendChart data={data.trend} threshold={50} className="mb-2" />
      {data.alerts.length > 0 && (
        <div className="mb-1 text-amber-300 border border-amber-500/30 bg-amber-500/10 rounded px-1.5 py-0.5 space-y-0.5">
          {data.alerts.map((a, i) => (
            <div key={i}>⚠ {a}</div>
          ))}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead className="text-slate-500">
            <tr>
              <th className="text-left px-1 py-0.5">时间</th>
              <th className="text-right px-1 py-0.5">n</th>
              <th className="text-left px-1 py-0.5">指标·RMSE</th>
            </tr>
          </thead>
          <tbody>
            {data.trend.slice(-5).map((t, idx) => (
              <tr key={idx} className="border-t border-edge/30">
                <td className="px-1 py-0.5 text-slate-400 font-mono text-[9px]">{t.at ? new Date(t.at).toLocaleString() : "-"}</td>
                <td className="px-1 py-0.5 text-right text-slate-300">{t.n_rows}</td>
                <td className="px-1 py-0.5 text-slate-400">
                  {Object.entries(t.by_metric)
                    .map(([m, v]) => `${m}:${v.rmse.toFixed(1)}`)
                    .join(" · ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
