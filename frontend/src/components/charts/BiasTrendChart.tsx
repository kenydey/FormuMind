import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import ChartContainer from "./ChartContainer";

interface BiasTrendData {
  at: string | null;
  n_rows: number;
  by_metric: Record<string, { n: number; mean_error: number; rmse: number; mae: number; max_abs: number }>;
}

interface BiasTrendChartProps {
  data: BiasTrendData[];
  threshold?: number;
  className?: string;
}

export default function BiasTrendChart({ data, threshold = 50, className = "" }: BiasTrendChartProps) {
  if (!data || data.length === 0) return null;

  const metrics = Array.from(new Set(data.flatMap((t) => Object.keys(t.by_metric))));
  const primary = metrics[0] || "rmse";

  const chartData = data.map((t, i) => {
    const row: Record<string, number | string> = {
      round: i + 1,
      label: `R${i + 1}(${t.n_rows})`,
    };
    for (const m of metrics) {
      row[`${m}_rmse`] = t.by_metric[m]?.rmse ?? 0;
      row[`${m}_mae`] = t.by_metric[m]?.mae ?? 0;
    }
    return row;
  });

  const colors = ["#f59e0b", "#38bdf8", "#a78bfa", "#34d399", "#f87171"];

  return (
    <ChartContainer title={`预测偏差趋势 · ${primary}`} className={className}>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.5} />
          <XAxis dataKey="label" stroke="#475569" tick={{ fill: "#94a3b8", fontSize: 10 }} />
          <YAxis stroke="#475569" tick={{ fill: "#94a3b8", fontSize: 10 }} />
          <Tooltip
            contentStyle={{
              backgroundColor: "#111722",
              border: "1px solid #1e2733",
              borderRadius: 6,
              fontSize: 11,
            }}
            labelStyle={{ color: "#e2e8f0" }}
            itemStyle={{ color: "#94a3b8" }}
          />
          <Legend wrapperStyle={{ fontSize: 10, color: "#94a3b8" }} />
          <ReferenceLine y={threshold} stroke="#f59e0b" strokeDasharray="4 4" label={{ value: `阈值 ${threshold}`, fill: "#f59e0b", fontSize: 10, position: "insideTopRight" }} />
          {metrics.map((m, i) => (
            <Line
              key={m}
              type="monotone"
              dataKey={`${m}_rmse`}
              name={`${m} RMSE`}
              stroke={colors[i % colors.length]}
              strokeWidth={2}
              dot={{ r: 3 }}
              activeDot={{ r: 5 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartContainer>
  );
}
