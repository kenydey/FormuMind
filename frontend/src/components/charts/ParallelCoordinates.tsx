import { useState, useMemo, useCallback, useRef } from "react";
import { CHART_THEME, linearScale, niceDomain } from "./chartUtils";
import ChartContainer from "./ChartContainer";
import type { Formulation } from "../../api";

interface AxisSpec {
  key: string;
  label: string;
  direction: "maximize" | "minimize";
  domain?: [number, number];
  unit?: string;
}

interface ParallelCoordinatesProps {
  formulations: Formulation[];
  axes: AxisSpec[];
  onBrush?: (filtered: Formulation[]) => void;
  highlightIds?: Set<string>;
  className?: string;
}

interface BrushRange {
  axisIndex: number;
  min: number;
  max: number;
}

export default function ParallelCoordinates({
  formulations,
  axes,
  onBrush,
  highlightIds,
  className = "",
}: ParallelCoordinatesProps) {
  const [brushes, setBrushes] = useState<BrushRange[]>([]);
  const [dragging, setDragging] = useState<{ axis: number; y0: number; y1: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const margin = { top: 30, right: 30, bottom: 20, left: 30 };
  const width = 600;
  const height = 320;
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  const axisPositions = useMemo(() => {
    return axes.map((_, i) => margin.left + (i * innerWidth) / Math.max(1, axes.length - 1));
  }, [axes, margin.left, innerWidth]);

  const scales = useMemo(() => {
    return axes.map((axis) => {
      const values = formulations
        .map((f) => f.predicted?.[axis.key] ?? f.measured?.[axis.key])
        .filter((v): v is number => v != null);
      const domain = axis.domain ?? niceDomain(values, 0.02);
      const range: [number, number] =
        axis.direction === "maximize" ? [innerHeight, 0] : [0, innerHeight];
      return { domain, scale: linearScale(domain, range) };
    });
  }, [axes, formulations, innerHeight]);

  const filteredFormulations = useMemo(() => {
    if (brushes.length === 0) return formulations;
    return formulations.filter((f) => {
      return brushes.every((brush) => {
        const axis = axes[brush.axisIndex];
        const v = f.predicted?.[axis.key] ?? f.measured?.[axis.key];
        if (v == null) return false;
        const s = scales[brush.axisIndex].scale(Number(v));
        const minY = Math.min(brush.min, brush.max);
        const maxY = Math.max(brush.min, brush.max);
        return s >= minY && s <= maxY;
      });
    });
  }, [formulations, brushes, axes, scales]);

  const handleMouseDown = useCallback(
    (axisIndex: number, e: React.MouseEvent) => {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const y = e.clientY - rect.top - margin.top;
      setDragging({ axis: axisIndex, y0: y, y1: y });
    },
    [margin.top]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging) return;
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const y = e.clientY - rect.top - margin.top;
      setDragging({ ...dragging, y1: y });
    },
    [dragging, margin.top]
  );

  const handleMouseUp = useCallback(() => {
    if (!dragging) return;
    const minY = Math.max(0, Math.min(dragging.y0, dragging.y1));
    const maxY = Math.min(innerHeight, Math.max(dragging.y0, dragging.y1));
    if (maxY - minY > 5) {
      setBrushes((prev) => [
        ...prev.filter((b) => b.axisIndex !== dragging.axis),
        { axisIndex: dragging.axis, min: minY, max: maxY },
      ]);
      onBrush?.(filteredFormulations);
    }
    setDragging(null);
  }, [dragging, innerHeight, onBrush, filteredFormulations]);

  const clearBrushes = () => {
    setBrushes([]);
    onBrush?.(formulations);
  };

  const renderPolyline = (f: Formulation, idx: number) => {
    const points = axes
      .map((axis, i) => {
        const v = f.predicted?.[axis.key] ?? f.measured?.[axis.key];
        if (v == null) return null;
        const x = axisPositions[i];
        const y = scales[i].scale(Number(v));
        return `${x},${y}`;
      })
      .filter(Boolean) as string[];

    if (points.length < 2) return null;

    const isHighlighted = highlightIds?.has(f.name);
    const isFiltered = filteredFormulations.includes(f);
    const opacity = brushes.length > 0 && !isFiltered ? 0.05 : isHighlighted ? 1 : 0.6;
    const strokeWidth = isHighlighted ? 2.5 : 1;
    const stroke = isHighlighted ? CHART_THEME.accent : CHART_THEME.accent2;

    return (
      <polyline
        key={`${f.name}-${idx}`}
        points={points.join(" ")}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        opacity={opacity}
        style={{ transition: "opacity 0.2s" }}
      />
    );
  };

  return (
    <ChartContainer
      title="配方多维对比（平行坐标）"
      className={className}
      actions={
        brushes.length > 0 ? (
          <button
            onClick={clearBrushes}
            className="text-[10px] text-slate-400 hover:text-accent border border-edge rounded px-2 py-0.5"
          >
            重置刷选 ({filteredFormulations.length}/{formulations.length})
          </button>
        ) : (
          <span className="text-[10px] text-slate-500">{formulations.length} 配方</span>
        )
      }
    >
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        className="w-full cursor-crosshair"
        style={{ height: 280 }}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        <g transform={`translate(${margin.left},${margin.top})`}>
          {/* Axes */}
          {axes.map((axis, i) => (
            <g key={axis.key}>
              <line
                x1={axisPositions[i] - margin.left}
                y1={0}
                x2={axisPositions[i] - margin.left}
                y2={innerHeight}
                stroke={CHART_THEME.axis}
                strokeWidth={1}
              />
              <text
                x={axisPositions[i] - margin.left}
                y={-10}
                textAnchor="middle"
                fill={CHART_THEME.textHighlight}
                fontSize={11}
                fontWeight={500}
              >
                {axis.label}
              </text>
              {/* Tick labels */}
              {[0, 0.25, 0.5, 0.75, 1].map((t) => {
                const y = t * innerHeight;
                const val =
                  axis.direction === "maximize"
                    ? scales[i].domain[0] + t * (scales[i].domain[1] - scales[i].domain[0])
                    : scales[i].domain[1] - t * (scales[i].domain[1] - scales[i].domain[0]);
                return (
                  <g key={t}>
                    <line
                      x1={axisPositions[i] - margin.left - 4}
                      y1={y}
                      x2={axisPositions[i] - margin.left}
                      y2={y}
                      stroke={CHART_THEME.axis}
                    />
                    <text
                      x={axisPositions[i] - margin.left - 8}
                      y={y + 3}
                      textAnchor="end"
                      fill={CHART_THEME.text}
                      fontSize={9}
                    >
                      {val.toFixed(1)}
                      {axis.unit}
                    </text>
                  </g>
                );
              })}
              {/* Brush interaction area */}
              <rect
                x={axisPositions[i] - margin.left - 15}
                y={0}
                width={30}
                height={innerHeight}
                fill="transparent"
                onMouseDown={(e) => handleMouseDown(i, e)}
              />
            </g>
          ))}

          {/* Polylines */}
          {formulations.map((f, i) => renderPolyline(f, i))}

          {/* Brush overlays */}
          {brushes.map((b) => (
            <rect
              key={b.axisIndex}
              x={axisPositions[b.axisIndex] - margin.left - 12}
              y={Math.min(b.min, b.max)}
              width={24}
              height={Math.abs(b.max - b.min)}
              fill={CHART_THEME.selection}
              stroke={CHART_THEME.accent}
              strokeWidth={1}
              rx={2}
            />
          ))}

          {/* Active drag */}
          {dragging && (
            <rect
              x={axisPositions[dragging.axis] - margin.left - 12}
              y={Math.min(dragging.y0, dragging.y1)}
              width={24}
              height={Math.abs(dragging.y1 - dragging.y0)}
              fill={CHART_THEME.selection}
              stroke={CHART_THEME.accent}
              strokeWidth={1}
              strokeDasharray="4 2"
              rx={2}
            />
          )}
        </g>
      </svg>
      <div className="flex gap-3 text-[10px] text-slate-500 mt-1">
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 bg-accent2 inline-block" /> 配方线
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ background: CHART_THEME.selection, border: `1px solid ${CHART_THEME.accent}` }} /> 刷选范围
        </span>
      </div>
    </ChartContainer>
  );
}
