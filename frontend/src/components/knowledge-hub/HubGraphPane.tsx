import { useCallback, useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  api,
  formatApiError,
  type KgMaterialGraphEdge,
  type KgMaterialGraphMeta,
  type KgMaterialGraphNode,
} from "../../api";
import { useStore } from "../../store";
import KgRelationPanel from "../KgRelationPanel";
import KgFeedbackStatsStrip from "../KgFeedbackStatsStrip";
import WikiPageGraphCanvas from "./WikiPageGraphCanvas";

const DEFAULT_TYPES = "substitutes,measured_*";

type ViewMode = "stats" | "canvas";

/** Graph pane: materials KG probe — stats dump or SQLite link canvas (P2). */
export default function HubGraphPane({ active }: { active: boolean }) {
  const activeProjectId = useStore(useShallow((s) => s.activeProjectId));
  const openSettings = useStore((s) => s.openSettings);
  const [viewMode, setViewMode] = useState<ViewMode>("canvas");
  const [kg, setKg] = useState<Record<string, unknown> | null>(null);
  const [neo, setNeo] = useState<Record<string, unknown> | null>(null);
  const [compounds, setCompounds] = useState<
    { uid?: string; name?: string | null; cas_number?: string | null }[]
  >([]);
  const [nodes, setNodes] = useState<KgMaterialGraphNode[]>([]);
  const [edges, setEdges] = useState<KgMaterialGraphEdge[]>([]);
  const [meta, setMeta] = useState<KgMaterialGraphMeta | null>(null);
  const [relationTypes, setRelationTypes] = useState(DEFAULT_TYPES);
  const [selected, setSelected] = useState<KgMaterialGraphNode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refreshStats = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const stats = await api.kgStats();
      setKg(stats as unknown as Record<string, unknown>);
    } catch (e) {
      setKg(null);
      setError(formatApiError(e));
    }
    try {
      const ns = await api.neo4jStats();
      setNeo(ns as unknown as Record<string, unknown>);
      const rows = await api.neo4jCompounds("", 40);
      setCompounds(Array.isArray(rows) ? rows : []);
    } catch {
      setNeo(null);
      setCompounds([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshCanvas = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.kgGraph({
        relation_types: relationTypes || DEFAULT_TYPES,
        limit: 400,
      });
      setNodes(r.nodes ?? []);
      setEdges(r.edges ?? []);
      setMeta(r.meta ?? null);
    } catch (e) {
      setNodes([]);
      setEdges([]);
      setMeta(null);
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [relationTypes]);

  const refresh = useCallback(async () => {
    if (!activeProjectId) {
      setKg(null);
      setNeo(null);
      setCompounds([]);
      setNodes([]);
      setEdges([]);
      setMeta(null);
      setSelected(null);
      setError(null);
      setLoading(false);
      return;
    }
    if (viewMode === "canvas") await refreshCanvas();
    else await refreshStats();
  }, [activeProjectId, viewMode, refreshCanvas, refreshStats]);

  useEffect(() => {
    if (active) void refresh();
  }, [active, refresh]);

  const canvasNodes = useMemo(
    () =>
      nodes.map((n) => ({
        id: n.id,
        path: n.id,
        label: n.label,
        kind: n.kind,
        degree: n.degree,
      })),
    [nodes],
  );

  const canvasEdges = useMemo(
    () =>
      edges.map((e) => ({
        source: e.source,
        target: e.target,
        weight: e.weight,
      })),
    [edges],
  );

  const isKgOff =
    !!error &&
    (error.toLowerCase().includes("kg_enabled") ||
      error.toLowerCase().includes("知识图谱未启用"));

  return (
    <div className="flex flex-col gap-2 h-full min-h-0" data-testid="hub-graph-pane">
      <div
        className="text-[10px] text-amber-200/90 border border-amber-500/30 rounded px-2 py-1"
        data-testid="hub-graph-project-scope"
      >
        {activeProjectId
          ? `材料关系（配方 KG · SQLite）· 全局语料预览（尚未按项目切分）· 与 Wiki「链接图」分离 · project=${activeProjectId}`
          : "未选择活动项目 — 请先打开/选择项目后再查看材料图谱"}
      </div>

      <div
        className="flex items-center gap-1 text-xs shrink-0"
        data-testid="hub-graph-view-toggle"
        role="tablist"
        aria-label="图谱视图"
      >
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === "canvas"}
          className={`px-2 py-1 border rounded ${
            viewMode === "canvas"
              ? "border-accent/60 text-accent bg-accent/10"
              : "border-edge text-slate-400"
          }`}
          data-testid="hub-graph-view-canvas"
          onClick={() => setViewMode("canvas")}
        >
          画布
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === "stats"}
          className={`px-2 py-1 border rounded ${
            viewMode === "stats"
              ? "border-accent/60 text-accent bg-accent/10"
              : "border-edge text-slate-400"
          }`}
          data-testid="hub-graph-view-stats"
          onClick={() => setViewMode("stats")}
        >
          统计
        </button>
        <button
          type="button"
          className="ml-auto text-xs px-2 py-1 border border-edge rounded disabled:opacity-40"
          disabled={!activeProjectId || loading}
          onClick={() => void refresh()}
          data-testid="hub-graph-refresh"
        >
          {loading ? "加载…" : "刷新"}
        </button>
      </div>

      {!activeProjectId ? (
        <p className="text-xs text-slate-500">选择活动项目后可刷新全局材料图谱（只读）。</p>
      ) : (
        <>
          {error && (
            <div className="text-xs text-rose-300 border border-rose-500/40 rounded px-2 py-1 space-y-1">
              <div>{error}</div>
              {isKgOff && (
                <button
                  type="button"
                  className="text-[11px] px-2 py-1 border border-amber-400/50 rounded text-amber-100"
                  data-testid="hub-graph-open-env"
                  onClick={() => openSettings("env", { focusEnvAttr: "kg_enabled" })}
                >
                  打开环境变量（kg_enabled）
                </button>
              )}
            </div>
          )}

          {viewMode === "canvas" ? (
            <div className="flex flex-col gap-2 flex-1 min-h-0" data-testid="hub-graph-canvas-mode">
              <KgFeedbackStatsStrip compact />
              <div className="flex flex-wrap items-center gap-2 text-xs shrink-0">
                <label className="text-slate-400 flex items-center gap-1">
                  关系类型
                  <input
                    className="bg-ink border border-edge rounded px-2 py-1 text-slate-200 min-w-[14rem] font-mono text-[11px]"
                    value={relationTypes}
                    onChange={(e) => setRelationTypes(e.target.value)}
                    onBlur={() => void refreshCanvas()}
                    data-testid="hub-graph-relation-types"
                    title="逗号分隔；支持 measured_*"
                  />
                </label>
              </div>
              {meta && (
                <div className="text-[10px] text-slate-500" data-testid="hub-graph-meta">
                  节点 {meta.node_count} · 边 {meta.edge_count}
                  {meta.scanned_links != null ? ` · 扫描链接 ${meta.scanned_links}` : ""}
                  {meta.elapsed_ms != null ? ` · ${meta.elapsed_ms}ms` : ""}
                  {meta.truncated ? " · 已截断" : ""}
                  {meta.backend ? ` · ${meta.backend}` : ""}
                  {meta.relation_types?.length
                    ? ` · [${meta.relation_types.join(", ")}]`
                    : ""}
                </div>
              )}
              <div className="grid grid-cols-1 lg:grid-cols-[1fr_16rem] gap-2 flex-1 min-h-0">
                <div className="relative min-h-[280px] min-w-0">
                  {!loading && nodes.length === 0 && !error && (
                    <div className="absolute inset-0 z-10 flex items-center justify-center text-xs text-slate-500 pointer-events-none">
                      无材料关系边 — 可重建关系层或放宽关系类型
                    </div>
                  )}
                  <WikiPageGraphCanvas
                    nodes={canvasNodes}
                    edges={canvasEdges}
                    selectedPath={selected?.id}
                    onSelect={(id) => {
                      const hit = nodes.find((n) => n.id === id) || null;
                      setSelected(hit);
                    }}
                  />
                </div>
                <aside
                  className="border border-edge/50 rounded p-2 overflow-y-auto min-h-0 text-xs"
                  data-testid="hub-graph-entity-panel"
                >
                  <h3 className="text-[11px] text-slate-200 mb-1">实体关系</h3>
                  {selected ? (
                    <>
                      <div className="text-[10px] text-slate-400 mb-2 break-all">
                        <div className="text-slate-200">{selected.label}</div>
                        <code>{selected.id}</code>
                        <div>
                          {selected.kind} · deg {selected.degree ?? 0}
                        </div>
                      </div>
                      <KgRelationPanel query={selected.label || selected.id} />
                    </>
                  ) : (
                    <p className="text-slate-500 text-[11px]">点击画布节点查看替代/矛盾关系</p>
                  )}
                </aside>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-3 flex-1 min-h-0 overflow-y-auto" data-testid="hub-graph-stats-mode">
              <section className="border border-edge/60 rounded p-3 text-xs space-y-1">
                <h3 className="text-sm text-slate-200 mb-1">SQLite 知识图谱（全局探针）</h3>
                {kg ? (
                  <pre className="text-slate-400 whitespace-pre-wrap font-mono text-[11px]">
                    {JSON.stringify(kg, null, 2)}
                  </pre>
                ) : (
                  <p className="text-slate-500">无法读取 /api/kg/stats（可能未启用 kg_enabled）</p>
                )}
              </section>
              <section className="border border-edge/60 rounded p-3 text-xs space-y-2">
                <h3 className="text-sm text-slate-200 mb-1">Neo4j（可选 · 全局 · 非画布依赖）</h3>
                {neo ? (
                  <>
                    <pre className="text-slate-400 whitespace-pre-wrap font-mono text-[11px]">
                      {JSON.stringify(neo, null, 2)}
                    </pre>
                    {compounds.length > 0 && (
                      <table className="w-full text-left mt-2">
                        <thead className="text-slate-500">
                          <tr>
                            <th className="py-1">名称</th>
                            <th>CAS</th>
                            <th>uid</th>
                          </tr>
                        </thead>
                        <tbody>
                          {compounds.map((c, i) => (
                            <tr key={c.uid || String(i)} className="border-t border-edge/30">
                              <td className="py-1 text-slate-300">{c.name || "—"}</td>
                              <td className="text-slate-400">{c.cas_number || "—"}</td>
                              <td className="font-mono text-[10px] text-slate-500">{c.uid}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </>
                ) : (
                  <p className="text-slate-500">Neo4j 未连接或不可用（画布仍可用 SQLite KG）</p>
                )}
              </section>
            </div>
          )}
        </>
      )}
    </div>
  );
}
