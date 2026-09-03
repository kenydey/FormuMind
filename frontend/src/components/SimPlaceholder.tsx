import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { useMemo } from "react";
import { useStore } from "../store";

const RMSE_COLORS = ["#38bdf8", "#a78bfa", "#34d399", "#fb923c", "#f472b6"];

function ConvergenceChart({ history }: { history: number[] }) {
  const data = history.map((v, i) => ({ iter: i + 1, score: v }));
  return (
    <div className="w-full h-full flex flex-col">
      <div className="text-[11px] text-slate-400 mb-1 flex justify-between">
        <span>最优目标得分（best-so-far）</span>
        <span className="font-mono text-accent2">{history[history.length - 1]?.toFixed(3)}</span>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
            <XAxis
              dataKey="iter"
              tick={{ fill: "#64748b", fontSize: 10 }}
              label={{ value: "迭代", position: "insideBottomRight", offset: -4, fill: "#64748b", fontSize: 10 }}
            />
            <YAxis tick={{ fill: "#64748b", fontSize: 10 }} domain={["auto", "auto"]} />
            <Tooltip
              contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 6, fontSize: 11 }}
              labelStyle={{ color: "#94a3b8" }}
              itemStyle={{ color: "#38bdf8" }}
              formatter={(v: number) => [v.toFixed(4), "score"]}
            />
            <Line
              type="monotone"
              dataKey="score"
              stroke="#38bdf8"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 3, fill: "#38bdf8" }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function RmseHistoryChart({ history }: { history: Record<string, number>[] }) {
  const metrics = useMemo(() => {
    const keys = new Set<string>();
    history.forEach((snap) => Object.keys(snap).forEach((k) => keys.add(k)));
    return [...keys];
  }, [history]);

  const data = useMemo(
    () =>
      history.map((snap, i) => ({
        round: i + 1,
        ...snap,
      })),
    [history]
  );

  if (metrics.length === 0) return null;

  return (
    <div className="w-full h-full flex flex-col">
      <div className="text-[11px] text-slate-400 mb-1 flex justify-between">
        <span>模型 RMSE 时序（闭环轮次）</span>
        <span className="font-mono text-slate-500">{history.length} 轮</span>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
            <XAxis dataKey="round" tick={{ fill: "#64748b", fontSize: 10 }} />
            <YAxis tick={{ fill: "#64748b", fontSize: 10 }} domain={["auto", "auto"]} />
            <Tooltip
              contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 6, fontSize: 11 }}
              labelStyle={{ color: "#94a3b8" }}
            />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            {metrics.map((metric, idx) => (
              <Line
                key={metric}
                type="monotone"
                dataKey={metric}
                name={metric}
                stroke={RMSE_COLORS[idx % RMSE_COLORS.length]}
                strokeWidth={1.5}
                dot={{ r: 2 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default function SimPlaceholder() {
  const history = useStore((s) => s.optimizationHistory);
  const rmseHistory = useStore((s) => s.rmseHistory);

  if (rmseHistory.length > 1) {
    return (
      <div className="glass rounded-xl p-4 flex flex-col h-full">
        <h2 className="text-sm uppercase tracking-widest text-accent2 mb-2">
          模型 RMSE · Loop RMSE
        </h2>
        <div className="flex-1 min-h-0">
          <RmseHistoryChart history={rmseHistory} />
        </div>
      </div>
    );
  }

  if (history.length > 0) {
    return (
      <div className="glass rounded-xl p-4 flex flex-col">
        <h2 className="text-sm uppercase tracking-widest text-accent2 mb-2">
          寻优收敛 · Convergence
        </h2>
        <div className="flex-1 min-h-0">
          <ConvergenceChart history={history} />
        </div>
      </div>
    );
  }

  return (
    <div className="glass rounded-xl p-4 flex flex-col items-center justify-center text-center relative overflow-hidden">
      <div className="absolute inset-0 opacity-20 bg-[radial-gradient(circle_at_50%_40%,#22d3ee,transparent_60%)]" />
      <div className="relative z-10">
        <div className="text-accent2 text-sm uppercase tracking-widest mb-2">寻优收敛 · Convergence</div>
        <div className="grid grid-cols-6 gap-1 mb-3 mx-auto w-fit">
          {Array.from({ length: 24 }).map((_, i) => (
            <div key={i} className="w-2.5 h-2.5 rounded-full bg-accent/30 animate-pulse" style={{ animationDelay: `${i * 60}ms` }} />
          ))}
        </div>
        <p className="text-xs text-slate-500 max-w-xs">
          运行贝叶斯寻优或闭环迭代后，此处展示收敛曲线；多轮闭环后展示 RMSE 时序图。
        </p>
      </div>
    </div>
  );
}
