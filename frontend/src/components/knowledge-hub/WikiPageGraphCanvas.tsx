import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { WikiPageGraphEdge, WikiPageGraphNode } from "../../api";
import { kindColor } from "../../wiki/wikiPageGraph";

type Pos = { x: number; y: number };

type Props = {
  nodes: WikiPageGraphNode[];
  edges: WikiPageGraphEdge[];
  selectedPath?: string | null;
  onSelect?: (path: string) => void;
};

/**
 * Lightweight SVG force canvas for Wiki page [[wikilink]] graph (P0).
 * Self-written layout — not materials KG; no GPL code.
 */
export default function WikiPageGraphCanvas({
  nodes,
  edges,
  selectedPath,
  onSelect,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 640, h: 420 });
  const [pos, setPos] = useState<Record<string, Pos>>({});
  const [dragId, setDragId] = useState<string | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const RO = typeof window !== "undefined" ? window.ResizeObserver : undefined;
    if (!RO) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 40 && rect.height > 40) {
        setSize({ w: rect.width, h: rect.height });
      }
      return;
    }
    const ro = new RO((entries) => {
      const cr = entries[0]?.contentRect;
      if (cr && cr.width > 40 && cr.height > 40) {
        setSize({ w: cr.width, h: cr.height });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Seed / reseed positions when node set changes
  useEffect(() => {
    const ids = nodes.map((n) => n.id || n.path);
    setPos((prev) => {
      const next: Record<string, Pos> = {};
      const cx = size.w / 2;
      const cy = size.h / 2;
      const r = Math.min(size.w, size.h) * 0.32;
      ids.forEach((id, i) => {
        if (prev[id]) {
          next[id] = prev[id];
          return;
        }
        const a = (i / Math.max(ids.length, 1)) * Math.PI * 2;
        next[id] = {
          x: cx + Math.cos(a) * r + ((i % 5) - 2) * 4,
          y: cy + Math.sin(a) * r + ((i % 3) - 1) * 4,
        };
      });
      return next;
    });
  }, [nodes, size.w, size.h]);

  // Simple force ticks (no WebGL / no GPL)
  useEffect(() => {
    if (nodes.length === 0 || dragId) return;
    let cancelled = false;
    let frame = 0;
    const tick = () => {
      if (cancelled) return;
      frame += 1;
      if (frame > 80) return;
      setPos((prev) => {
        const ids = nodes.map((n) => n.id || n.path);
        const next: Record<string, Pos> = { ...prev };
        for (const id of ids) {
          if (!next[id]) next[id] = { x: size.w / 2, y: size.h / 2 };
        }
        for (let i = 0; i < ids.length; i++) {
          for (let j = i + 1; j < ids.length; j++) {
            const a = ids[i];
            const b = ids[j];
            const pa = next[a];
            const pb = next[b];
            let dx = pa.x - pb.x;
            let dy = pa.y - pb.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
            const force = 900 / (dist * dist);
            dx = (dx / dist) * force;
            dy = (dy / dist) * force;
            pa.x += dx;
            pa.y += dy;
            pb.x -= dx;
            pb.y -= dy;
          }
        }
        for (const e of edges) {
          const pa = next[e.source];
          const pb = next[e.target];
          if (!pa || !pb) continue;
          const dx = pb.x - pa.x;
          const dy = pb.y - pa.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
          const f = (dist - 90) * 0.02;
          const ox = (dx / dist) * f;
          const oy = (dy / dist) * f;
          pa.x += ox;
          pa.y += oy;
          pb.x -= ox;
          pb.y -= oy;
        }
        const cx = size.w / 2;
        const cy = size.h / 2;
        for (const id of ids) {
          const p = next[id];
          p.x += (cx - p.x) * 0.01;
          p.y += (cy - p.y) * 0.01;
          p.x = Math.max(24, Math.min(size.w - 24, p.x));
          p.y = Math.max(24, Math.min(size.h - 24, p.y));
        }
        return next;
      });
      requestAnimationFrame(tick);
    };
    const id = requestAnimationFrame(tick);
    return () => {
      cancelled = true;
      cancelAnimationFrame(id);
    };
  }, [nodes, edges, size.w, size.h, dragId]);

  const onPointerDown = (id: string, path: string, ev: ReactPointerEvent) => {
    const target = ev.currentTarget as Element & {
      setPointerCapture?: (pointerId: number) => void;
    };
    target.setPointerCapture?.(ev.pointerId);
    setDragId(id);
    onSelect?.(path);
  };

  const onPointerMove = (ev: ReactPointerEvent) => {
    if (!dragId) return;
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    setPos((prev) => ({
      ...prev,
      [dragId]: {
        x: Math.max(16, Math.min(size.w - 16, x)),
        y: Math.max(16, Math.min(size.h - 16, y)),
      },
    }));
  };

  const onPointerUp = () => setDragId(null);

  return (
    <div
      ref={wrapRef}
      className="relative w-full h-full min-h-[280px] bg-ink/40 rounded border border-edge/50 overflow-hidden touch-none"
      data-testid="wiki-page-graph-canvas"
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerUp}
    >
      <svg width={size.w} height={size.h} className="block select-none">
        <defs>
          <marker
            id="wiki-graph-arrow"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569" />
          </marker>
        </defs>
        {edges.map((e) => {
          const a = pos[e.source];
          const b = pos[e.target];
          if (!a || !b) return null;
          return (
            <line
              key={`${e.source}->${e.target}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke="#475569"
              strokeWidth={1.2}
              markerEnd="url(#wiki-graph-arrow)"
              opacity={0.85}
            />
          );
        })}
        {nodes.map((n) => {
          const id = n.id || n.path;
          const p = pos[id];
          if (!p) return null;
          const selected = selectedPath === n.path || selectedPath === id;
          const r = 8 + Math.min(10, (n.degree ?? 0) * 1.5);
          const fill = kindColor(n.kind);
          return (
            <g
              key={id}
              transform={`translate(${p.x},${p.y})`}
              style={{ cursor: "pointer" }}
              data-testid={`wiki-graph-node-${n.path}`}
              data-path={n.path}
              onPointerDown={(ev) => onPointerDown(id, n.path, ev)}
              onClick={(ev) => {
                ev.stopPropagation();
                onSelect?.(n.path);
              }}
            >
              <circle
                r={r}
                fill={fill}
                stroke={selected ? "#f8fafc" : "#0f172a"}
                strokeWidth={selected ? 2.5 : 1.2}
                opacity={0.95}
                pointerEvents="all"
              />
              <title>{n.label || n.path}</title>
              <text
                y={r + 12}
                textAnchor="middle"
                className="fill-slate-300"
                style={{ fontSize: 10, pointerEvents: "none" }}
              >
                {(n.label || n.path).slice(0, 18)}
              </text>
            </g>
          );
        })}
      </svg>
      {nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center text-xs text-slate-500 pointer-events-none">
          无节点可显示
        </div>
      )}
    </div>
  );
}
