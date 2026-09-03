import { useState, useCallback } from "react";
import {
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Line,
  ComposedChart,
} from "recharts";
import ChartContainer from "./ChartContainer";
import { useParetoFront } from "../../hooks/useParetoFront";
import type { Formulation } from "../../api";

interface ParetoPoint {
  id: string;
  x: number;
  y: number;
  name: string;
  source: "predicted" | "measured";
  is_pareto: boolean;
  formulation: Formulation;
}

interface ParetoFrontPlotProps {
  formulations: Formulation[];
  xMetric: string;      // e.g. "cost_cny_per_kg"
  yMetric: string;      // e.g. "salt_spray_hours"
  xLabel?: string;
  yLabel?: string;
  xDirection?: "minimize" | "maximize";
  yDirection?: "minimize" | "maximize";
  onPointClick?: (form: Formulation) => void;
  className?: string;
}

export default function ParetoFrontPlot({
  formulations,
  xMetric,
  yMetric,
  xLabel = xMetric,
  yLabel = yMetric,
  xDirection = "minimize",
  yDirection = "maximize",
  onPointClick,
  className = "",
}: ParetoFrontPlotProps) {
  const [, setHoveredId] = useState<string | null>(null);

  const rawPoints = formulations
    .map((f, i) => {
      const x = f.predicted?.[xMetric] ?? f.measured?.[xMetric];
      const y = f.predicted?.[yMetric] ?? f.measured?.[yMetric];
      if (x == null || y == null) return null;
      return {
        id: `${f.name}-${i}`,
        x: Number(x),
        y: Number(y),
        name: f.name,
        source: f.measured?.[yMetric] != null ? ("measured" as const) : ("predicted" as const),
        formulation: f,
      };
    })
    .filter(Boolean) as Array<{
      id: string;
      x: number;
      y: number;
      name: string;
      source: "predicted" | "measured";
      formulation: Formulation;
    }>;

  const pointsWithPareto = useParetoFront(
    rawPoints.map((p) => ({ id: p.id, x: p.x, y: p.y, source: p.source, name: p.name, formulation: p.formulation })),
    xDirection,
    yDirection
  );

  const chartPoints = pointsWithPareto.map((p) => ({
    ...p,
    fill: p.source === "measured" ? "#34d399" : "#94a3b8",
    stroke: p.is_pareto ? "#fbbf24" : "transparent",
    strokeWidth: p.is_pareto ? 2 : 0,
    r: p.is_pareto ? 6 : 4,
  }));

  const paretoLine = chartPoints
    .filter((p) => p.is_pareto)
    .sort((a, b) => a.x - b.x)
    .map((p) => ({ x: p.x, y: p.y }));

  const handleClick = useCallback(
    (data: unknown) => {
      const pt = data as ParetoPoint | undefined;
      if (pt?.formulation && onPointClick) onPointClick(pt.formulation);
    },
    [onPointClick]
  );

  if (chartPoints.length === 0) {
    return (
      <ChartContainer title={`帕累托前沿 · ${xLabel} vs ${yLabel}`} className={className}>
        <div className="flex items-center justify-center h-48 text-slate-500 text-sm">
          无可视化数据（缺少 {xMetric} 或 {yMetric}）
        </div>
      </ChartContainer>
    );
  }

  return (
    <ChartContainer
      title={`帕累托前沿 · ${xLabel} vs ${yLabel}`}
      className={className}
      actions={
        <span className="text-[10px] text-slate-500">
          {chartPoints.filter((p) => p.is_pareto).length} / {chartPoints.length} 在前沿上
        </span>
      }
    >
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.5} />
          <XAxis
            dataKey="x"
            type="number"
            name={xLabel}
            stroke="#475569"
            tick={{ fill: "#94a3b8", fontSize: 10 }}
            label={{ value: xLabel, position: "insideBottom", offset: -5, fill: "#94a3b8", fontSize: 10 }}
          />
          <YAxis
            dataKey="y"
            type="number"
            name={yLabel}
            stroke="#475569"
            tick={{ fill: "#94a3b8", fontSize: 10 }}
            label={{ value: yLabel, angle: -90, position: "insideLeft", fill: "#94a3b8", fontSize: 10 }}
          />
          <Tooltip
            cursor={{ strokeDasharray: "3 3" }}
            contentStyle={{
              backgroundColor: "#111722",
              border: "1px solid #1e2733",
              borderRadius: 6,
              fontSize: 11,
            }}
            labelStyle={{ color: "#e2e8f0" }}
            formatter={(value: number, name: string, props: { payload?: ParetoPoint }) => {
              const pt = props.payload;
              if (!pt) return [value, name];
              return [
                `${pt.name} | ${xLabel}: ${pt.x.toFixed(1)} | ${yLabel}: ${pt.y.toFixed(1)}`,
                pt.is_pareto ? "★ 帕累托" : pt.source === "measured" ? "实测" : "预测",
              ];
            }}
          />
          <Legend wrapperStyle={{ fontSize: 10, color: "#94a3b8" }} />
          <Scatter
            name="预测"
            data={chartPoints.filter((p) => p.source === "predicted")}
            fill="#94a3b8"
            onClick={handleClick}
            onMouseEnter={(d) => setHoveredId((d as ParetoPoint).id)}
            onMouseLeave={() => setHoveredId(null)}
          />
          <Scatter
            name="实测"
            data={chartPoints.filter((p) => p.source === "measured")}
            fill="#34d399"
            onClick={handleClick}
            onMouseEnter={(d) => setHoveredId((d as ParetoPoint).id)}
            onMouseLeave={() => setHoveredId(null)}
          />
          {paretoLine.length > 1 && (
            <Line
              name="帕累托前沿"
              data={paretoLine}
              dataKey="y"
              type="linear"
              stroke="#fbbf24"
              strokeWidth={2}
              strokeDasharray="5 5"
              dot={false}
              activeDot={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </ChartContainer>
  );
}
