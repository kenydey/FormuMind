import type { ObjectiveSpec, WorkbenchRow } from "../api";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

interface ExperimentDetailProps {
  data: WorkbenchRow;
  objectives: ObjectiveSpec[];
}

const METRIC_COLORS = ["#22d3ee", "#a78bfa", "#fbbf24", "#34d399", "#f472b6", "#60a5fa"];

export default function ExperimentDetail({ data, objectives }: ExperimentDetailProps) {
  // Build measurement history from row data (multi-round snapshots if available)
  const historyData = (data as any).measurement_history as Array<Record<string, unknown>> | undefined;

  return (
    <div className="flex gap-4 p-3 bg-gray-900/50 min-h-[160px]">
      {/* Trend chart */}
      <div className="flex-1 min-w-0">
        <h4 className="text-[10px] font-semibold text-slate-400 mb-1">指标趋势</h4>
        {historyData && historyData.length > 0 ? (
          <ResponsiveContainer width="100%" height={120}>
            <LineChart data={historyData}>
              <XAxis dataKey="round" hide />
              <YAxis width={28} tick={{ fontSize: 9, fill: "#94a3b8" }} />
              <Tooltip
                contentStyle={{ fontSize: 10, background: "#1e293b", border: "1px solid #334155", borderRadius: 4 }}
                labelStyle={{ color: "#94a3b8" }}
              />
              {objectives.map((o, i) => (
                <Line
                  key={o.metric}
                  dataKey={o.metric}
                  name={o.display_name || o.metric}
                  stroke={METRIC_COLORS[i % METRIC_COLORS.length]}
                  strokeWidth={1.5}
                  dot={{ r: 2 }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-[10px] text-slate-500">暂无历史趋势数据</p>
        )}
      </div>

      {/* Attachments section */}
      <div className="w-40 text-[10px]">
        <h4 className="font-semibold text-slate-400 mb-1">附件</h4>
        {(data as any).attachments?.length ? (
          (data as any).attachments.map((a: any) => (
            <div key={a.id} className="text-slate-500 truncate mb-0.5">
              📎 {a.filename || a.source_document_id?.slice(0, 12)}
            </div>
          ))
        ) : (
          <p className="text-slate-600">—</p>
        )}
      </div>

      {/* Tags + Note section */}
      <div className="w-44 text-[10px]">
        <h4 className="font-semibold text-slate-400 mb-1">标签</h4>
        <div className="flex flex-wrap gap-0.5 mb-2">
          {data.tags?.length ? (
            data.tags.map((t) => (
              <span key={t} className="text-[9px] px-1.5 py-0.5 rounded bg-accent2/10 text-accent2 border border-accent2/30">
                {t}
              </span>
            ))
          ) : (
            <span className="text-slate-600">—</span>
          )}
        </div>
        <h4 className="font-semibold text-slate-400 mb-1 mt-1">备注</h4>
        <p className="text-slate-500 leading-relaxed">{data.note || "—"}</p>
      </div>
    </div>
  );
}

// Registry function for AG Grid detailCellRenderer
export function detailCellRenderer(params: any) {
  // objectives will be passed via context or hardcoded for now
  const objectives = (params as any).objectives || [];
  return <ExperimentDetail data={params.data} objectives={objectives} />;
}
