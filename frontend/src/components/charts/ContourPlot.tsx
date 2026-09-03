import { useMemo } from "react";
import { useContourGrid } from "../../hooks/useContourGrid";
import { CHART_THEME, linearScale, niceDomain } from "./chartUtils";
import ChartContainer from "./ChartContainer";

interface ContourDataPoint {
  x: number;
  y: number;
  z: number;
}

interface ContourPlotProps {
  data: ContourDataPoint[];
  xFactor: string;
  yFactor: string;
  metric: string;
  className?: string;
}

export default function ContourPlot({ data, xFactor, yFactor, metric, className = "" }: ContourPlotProps) {
  const margin = { top: 20, right: 20, bottom: 40, left: 50 };
  const width = 400;
  const height = 320;
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  const xValues = data.map((d) => d.x);
  const yValues = data.map((d) => d.y);
  const zValues = data.map((d) => d.z);

  const xDomain = niceDomain(xValues, 0.05);
  const yDomain = niceDomain(yValues, 0.05);
  const zMin = Math.min(...zValues);
  const zMax = Math.max(...zValues);

  const xScale = linearScale(xDomain, [0, innerWidth]);
  const yScale = linearScale(yDomain, [innerHeight, 0]);

  const { grid, gridX, gridY, paths, levelValues } = useContourGrid(data, xDomain, yDomain, 30, 8);

  const colorScale = useMemo(() => {
    return (z: number): string => {
      const t = (z - zMin) / (zMax - zMin || 1);
      if (t < 0.33) return CHART_THEME.contourLow;
      if (t < 0.67) return CHART_THEME.contourMid;
      return CHART_THEME.contourHigh;
    };
  }, [zMin, zMax]);

  if (data.length < 3) {
    return (
      <ChartContainer title={`响应曲面 · ${metric}`} className={className}>
        <div className="flex items-center justify-center h-48 text-slate-500 text-sm">
          数据点不足（需要 ≥3 个实验点）
        </div>
      </ChartContainer>
    );
  }

  return (
    <ChartContainer title={`响应曲面 · ${metric} (${xFactor} vs ${yFactor})`} className={className}>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ height: 280 }}>
        <g transform={`translate(${margin.left},${margin.top})`}>
          {/* Background grid cells with color interpolation */}
          {grid.length > 0 &&
            grid.map((row, yi) =>
              row.map((z, xi) => {
                if (xi >= gridX.length - 1 || yi >= gridY.length - 1) return null;
                const x0 = xScale(gridX[xi]);
                const x1 = xScale(gridX[xi + 1]);
                const y0 = yScale(gridY[yi]);
                const y1 = yScale(gridY[yi + 1]);
                return (
                  <rect
                    key={`${xi}-${yi}`}
                    x={x0}
                    y={y1}
                    width={x1 - x0}
                    height={y0 - y1}
                    fill={colorScale(z)}
                    opacity={0.6}
                  />
                );
              })
            )}

          {/* Contour lines */}
          {paths.map((level, li) => (
            <g key={li}>
              {level.paths.map((d, pi) => (
                <path
                  key={pi}
                  d={d}
                  fill="none"
                  stroke={colorScale(level.level)}
                  strokeWidth={1.5}
                  opacity={0.9}
                />
              ))}
            </g>
          ))}

          {/* Data points */}
          {data.map((d, i) => (
            <circle
              key={i}
              cx={xScale(d.x)}
              cy={yScale(d.y)}
              r={4}
              fill={colorScale(d.z)}
              stroke="#0a0e14"
              strokeWidth={1.5}
            >
              <title>{`${xFactor}: ${d.x.toFixed(1)}% | ${yFactor}: ${d.y.toFixed(1)}% | ${metric}: ${d.z.toFixed(1)}`}</title>
            </circle>
          ))}

          {/* X axis */}
          <line x1={0} y1={innerHeight} x2={innerWidth} y2={innerHeight} stroke={CHART_THEME.axis} />
          <text
            x={innerWidth / 2}
            y={innerHeight + 30}
            textAnchor="middle"
            fill={CHART_THEME.text}
            fontSize={10}
          >
            {xFactor} (%)
          </text>

          {/* Y axis */}
          <line x1={0} y1={0} x2={0} y2={innerHeight} stroke={CHART_THEME.axis} />
          <text
            x={-30}
            y={innerHeight / 2}
            textAnchor="middle"
            fill={CHART_THEME.text}
            fontSize={10}
            transform={`rotate(-90, -30, ${innerHeight / 2})`}
          >
            {yFactor} (%)
          </text>

          {/* Color legend */}
          <g transform={`translate(${innerWidth + 10}, 10)`}>
            <text x={0} y={-5} fill={CHART_THEME.text} fontSize={9}>
              {metric}
            </text>
            {[0, 0.25, 0.5, 0.75, 1].map((t, i) => {
              const z = zMin + t * (zMax - zMin);
              return (
                <g key={i} transform={`translate(0, ${i * 20})`}>
                  <rect width={12} height={12} fill={colorScale(z)} rx={2} />
                  <text x={18} y={10} fill={CHART_THEME.text} fontSize={9}>
                    {z.toFixed(0)}
                  </text>
                </g>
              );
            })}
          </g>
        </g>
      </svg>
    </ChartContainer>
  );
}
