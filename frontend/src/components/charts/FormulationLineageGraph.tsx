import { useMemo } from "react";
import { CHART_THEME } from "./chartUtils";
import ChartContainer from "./ChartContainer";

interface LineageNode {
  id: string;
  version: number;
  name: string;
  change_summary: string;
  snapshot: Record<string, unknown>;
  parent_id: string | null;
  children: string[];
}

interface FormulationLineageGraphProps {
  nodes: LineageNode[];
  onNodeClick?: (node: LineageNode) => void;
  className?: string;
}

export default function FormulationLineageGraph({
  nodes,
  onNodeClick,
  className = "",
}: FormulationLineageGraphProps) {
  const margin = { top: 20, right: 20, bottom: 20, left: 20 };
  const width = 500;
  const height = 300;
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  const layout = useMemo(() => {
    // Build parent map
    const nodeMap = new Map<string, LineageNode>();
    nodes.forEach((n) => nodeMap.set(n.id, n));

    // Find root(s)
    const roots = nodes.filter((n) => !n.parent_id);
    if (roots.length === 0 && nodes.length > 0) {
      roots.push(nodes[0]);
    }

    // BFS to assign levels
    const levels = new Map<string, number>();
    const queue = roots.map((r) => ({ id: r.id, level: 0 }));
    const visited = new Set<string>();
    while (queue.length > 0) {
      const { id, level } = queue.shift()!;
      if (visited.has(id)) continue;
      visited.add(id);
      levels.set(id, level);
      const node = nodeMap.get(id);
      if (node) {
        node.children.forEach((childId) => {
          if (!visited.has(childId)) {
            queue.push({ id: childId, level: level + 1 });
          }
        });
      }
    }

    // Group by level
    const levelGroups = new Map<number, string[]>();
    levels.forEach((level, id) => {
      if (!levelGroups.has(level)) levelGroups.set(level, []);
      levelGroups.get(level)!.push(id);
    });

    const maxLevel = Math.max(...Array.from(levels.values()), 0);
    const levelHeight = maxLevel > 0 ? innerHeight / maxLevel : innerHeight;

    // Calculate positions
    const positions = new Map<string, { x: number; y: number }>();
    levelGroups.forEach((ids, level) => {
      const count = ids.length;
      const step = count > 1 ? innerWidth / (count - 1) : innerWidth / 2;
      ids.forEach((id, i) => {
        const x = count > 1 ? i * step : innerWidth / 2;
        const y = level * levelHeight;
        positions.set(id, { x, y });
      });
    });

    return { positions, nodeMap, maxLevel };
  }, [nodes, innerWidth, innerHeight]);

  if (nodes.length === 0) {
    return (
      <ChartContainer title="配方谱系" className={className}>
        <div className="flex items-center justify-center h-48 text-slate-500 text-sm">
          无版本历史数据
        </div>
      </ChartContainer>
    );
  }

  const { positions } = layout;

  return (
    <ChartContainer title="配方谱系演进" className={className}>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ height: 260 }}>
        <g transform={`translate(${margin.left},${margin.top})`}>
          {/* Edges */}
          {nodes.map((node) => {
            if (!node.parent_id) return null;
            const start = positions.get(node.parent_id);
            const end = positions.get(node.id);
            if (!start || !end) return null;
            return (
              <line
                key={`edge-${node.id}`}
                x1={start.x}
                y1={start.y}
                x2={end.x}
                y2={end.y}
                stroke={CHART_THEME.grid}
                strokeWidth={1.5}
                markerEnd="url(#arrow)"
              />
            );
          })}

          {/* Arrow marker */}
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX={8} refY={5} markerWidth={6} markerHeight={6} orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={CHART_THEME.grid} />
            </marker>
          </defs>

          {/* Nodes */}
          {nodes.map((node) => {
            const pos = positions.get(node.id);
            if (!pos) return null;
            return (
              <g
                key={node.id}
                transform={`translate(${pos.x},${pos.y})`}
                className="cursor-pointer"
                onClick={() => onNodeClick?.(node)}
              >
                <circle
                  r={18}
                  fill="#111722"
                  stroke={CHART_THEME.accent}
                  strokeWidth={1.5}
                />
                <text
                  textAnchor="middle"
                  dy={-2}
                  fill={CHART_THEME.textHighlight}
                  fontSize={9}
                  fontWeight={600}
                >
                  v{node.version}
                </text>
                <text
                  textAnchor="middle"
                  dy={10}
                  fill={CHART_THEME.text}
                  fontSize={7}
                >
                  {node.change_summary.slice(0, 8)}
                </text>
                <title>
                  {`v${node.version}: ${node.name}\n${node.change_summary}`}
                </title>
              </g>
            );
          })}
        </g>
      </svg>
      <div className="text-[10px] text-slate-500 mt-1">
        点击节点查看配方详情 · 箭头表示版本演进方向
      </div>
    </ChartContainer>
  );
}
