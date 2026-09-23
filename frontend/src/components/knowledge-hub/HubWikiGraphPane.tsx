import { useCallback, useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  api,
  formatApiError,
  type WikiPageGraphEdge,
  type WikiPageGraphInsights,
  type WikiPageGraphMeta,
  type WikiPageGraphNode,
} from "../../api";
import { useStore } from "../../store";
import { filterWikiPageGraph, type WikiPageGraphColorMode } from "../../wiki/wikiPageGraph";
import HubWikiGraphInsights from "./HubWikiGraphInsights";
import WikiPageGraphCanvas from "./WikiPageGraphCanvas";

const FLAG_ATTR = "wiki_page_graph_enabled";

function isFlagGateError(msg: string): boolean {
  const m = (msg || "").toLowerCase();
  return (
    m.includes("wiki_page_graph_enabled") ||
    m.includes("wiki_enabled") ||
    m.includes("formumind_wiki_page_graph")
  );
}

type Props = {
  active: boolean;
  selectedPath?: string | null;
  onOpenPath: (path: string) => void;
};

/** Hub Wiki · 链接图 — page [[wikilink]] canvas (not materials KG). */
export default function HubWikiGraphPane({ active, selectedPath, onOpenPath }: Props) {
  const activeProjectId = useStore(useShallow((s) => s.activeProjectId));
  const openSettings = useStore((s) => s.openSettings);
  const envFlagsRevision = useStore((s) => s.envFlagsRevision);

  const [flagOn, setFlagOn] = useState<boolean | null>(null);
  const [flagsError, setFlagsError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<WikiPageGraphNode[]>([]);
  const [edges, setEdges] = useState<WikiPageGraphEdge[]>([]);
  const [meta, setMeta] = useState<WikiPageGraphMeta | null>(null);
  const [insights, setInsights] = useState<WikiPageGraphInsights | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hideOrphan, setHideOrphan] = useState(false);
  const [kind, setKind] = useState("");
  const [colorMode, setColorMode] = useState<WikiPageGraphColorMode>("kind");

  const loadFlags = useCallback(() => {
    void api
      .getEnvFlags()
      .then((body) => {
        const hit = (body.flags ?? []).find((f) => f.attr === FLAG_ATTR);
        setFlagOn(hit ? Boolean(hit.value) : false);
        setFlagsError(null);
      })
      .catch((e) => {
        setFlagOn(null);
        setFlagsError(formatApiError(e));
      });
  }, []);

  const refresh = useCallback(async () => {
    if (!activeProjectId) {
      setNodes([]);
      setEdges([]);
      setMeta(null);
      setInsights(null);
      setError("请先选择活动项目（页图按项目 scope 过滤）");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await api.getWikiPageGraph({
        limit: 400,
        include_orphan: true,
        kinds: kind || undefined,
        project_id: activeProjectId,
      });
      setNodes(r.nodes ?? []);
      setEdges(r.edges ?? []);
      setMeta(r.meta ?? null);
      setInsights(r.insights ?? null);
    } catch (e) {
      setNodes([]);
      setEdges([]);
      setMeta(null);
      setInsights(null);
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [activeProjectId, kind]);

  useEffect(() => {
    if (!active) return;
    loadFlags();
  }, [active, loadFlags, envFlagsRevision]);

  useEffect(() => {
    if (!active) return;
    if (flagOn === true) void refresh();
  }, [active, flagOn, refresh]);

  const filtered = useMemo(
    () =>
      filterWikiPageGraph(nodes, edges, {
        query,
        hideOrphan,
        kinds: kind ? [kind] : undefined,
      }),
    [nodes, edges, query, hideOrphan, kind],
  );

  const showFlagCta =
    flagOn === false || (!!error && isFlagGateError(error)) || !!flagsError;

  const runLint = useCallback(async () => {
    setBusy("lint");
    setActionMsg(null);
    setError(null);
    try {
      const out = await api.runWikiLint({ limit: 200, detect_orphan: true });
      setActionMsg(
        `Lint：扫描 ${out.scanned ?? "?"} · 有旗标 ${out.flagged ?? "?"} · 孤儿 ${out.orphan_count ?? "?"}`,
      );
      await refresh();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(null);
    }
  }, [refresh]);

  const refreshDossier = useCallback(async () => {
    if (!activeProjectId) {
      setError("请先选择活动项目");
      return;
    }
    setBusy("dossier");
    setActionMsg(null);
    setError(null);
    try {
      const out = await api.refreshWikiDossier({ project_id: activeProjectId });
      setActionMsg(
        out.path
          ? `卷宗已刷新：${out.path}`
          : "卷宗刷新完成",
      );
      if (out.path) onOpenPath(out.path);
      await refresh();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(null);
    }
  }, [activeProjectId, onOpenPath, refresh]);

  return (
    <div className="flex flex-col gap-2 h-full min-h-0" data-testid="hub-wiki-graph-pane">
      <div className="text-[10px] text-slate-400 border border-edge/50 rounded px-2 py-1">
        Wiki 页链接图（[[wikilink]]）· 非配方/材料 KG · 点节点打开 Reader
      </div>

      {showFlagCta && (
        <div
          className="text-xs text-amber-100/95 border border-amber-500/40 rounded px-2 py-2 space-y-1"
          data-testid="hub-wiki-graph-flag-cta"
        >
          <p>
            页图未启用或旗标读取失败。请在环境变量中打开{" "}
            <code className="text-amber-200">wiki_page_graph_enabled</code>
            （依赖 <code>wiki_enabled</code>）。
          </p>
          {flagsError && <p className="text-rose-300 text-[11px]">{flagsError}</p>}
          <button
            type="button"
            className="text-[11px] px-2 py-1 border border-amber-400/50 rounded text-amber-100"
            data-testid="hub-wiki-graph-open-env"
            onClick={() => openSettings("env", { focusEnvAttr: FLAG_ATTR })}
          >
            打开环境变量设置
          </button>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 text-xs shrink-0">
        <select
          className="bg-ink border border-edge rounded px-2 py-1 text-slate-200"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          data-testid="hub-wiki-graph-kind"
        >
          <option value="">全部类型</option>
          <option value="material">materials</option>
          <option value="chemical">chemicals</option>
          <option value="system">systems</option>
          <option value="mechanism">mechanisms</option>
          <option value="pitfall">pitfalls</option>
          <option value="theme">themes</option>
          <option value="report">reports</option>
        </select>
        <label className="flex items-center gap-1 text-slate-400">
          <input
            type="checkbox"
            checked={hideOrphan}
            onChange={(e) => setHideOrphan(e.target.checked)}
            data-testid="hub-wiki-graph-hide-orphan"
          />
          隐藏孤立
        </label>
        <select
          className="bg-ink border border-edge rounded px-2 py-1 text-slate-200"
          value={colorMode}
          onChange={(e) => setColorMode(e.target.value as WikiPageGraphColorMode)}
          data-testid="hub-wiki-graph-color-mode"
          title="节点着色：类型 / 弱连通社区"
        >
          <option value="kind">着色·类型</option>
          <option value="community">着色·社区</option>
        </select>
        <input
          type="search"
          className="bg-ink border border-edge rounded px-2 py-1 text-slate-200 min-w-[8rem] flex-1"
          placeholder="搜索标题 / path…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="hub-wiki-graph-search"
        />
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded disabled:opacity-40"
          disabled={loading || flagOn === false}
          onClick={() => void refresh()}
          data-testid="hub-wiki-graph-refresh"
        >
          {loading ? "加载…" : "刷新"}
        </button>
      </div>

      {error && !isFlagGateError(error) && (
        <div className="text-xs text-rose-300 border border-rose-500/40 rounded px-2 py-1">
          {error}
        </div>
      )}
      {actionMsg && (
        <div
          className="text-[10px] text-amber-200/90 border border-amber-500/30 rounded px-2 py-1"
          data-testid="hub-wiki-graph-action-msg"
        >
          {actionMsg}
        </div>
      )}

      {meta && (
        <div
          className="text-[10px] text-slate-500"
          data-testid="hub-wiki-graph-meta"
        >
          节点 {filtered.nodes.length}/{meta.node_count} · 边 {filtered.edges.length}/
          {meta.edge_count}
          {meta.broken_links != null ? ` · 断链 ${meta.broken_links}` : ""}
          {meta.orphan_count != null ? ` · 孤立 ${meta.orphan_count}` : ""}
          {meta.community_count != null ? ` · 社区 ${meta.community_count}` : ""}
          {meta.weighting ? ` · 边权 ${meta.weighting}` : ""}
          {meta.elapsed_ms != null ? ` · ${meta.elapsed_ms}ms` : ""}
          {meta.truncated ? " · 已截断" : ""}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_15rem] gap-2 flex-1 min-h-0">
        <div className="relative min-h-0 min-w-0">
          {flagOn === true && !loading && filtered.nodes.length === 0 && !error && (
            <div className="absolute inset-0 z-10 flex items-center justify-center text-xs text-slate-500 pointer-events-none">
              无链接可显示 — 可跑 Lint 或编译更多带 [[wikilink]] 的页
            </div>
          )}
          {flagOn === true && (
            <WikiPageGraphCanvas
              nodes={filtered.nodes}
              edges={filtered.edges}
              selectedPath={selectedPath}
              colorMode={colorMode}
              onSelect={onOpenPath}
            />
          )}
        </div>
        {flagOn === true && (
          <HubWikiGraphInsights
            insights={insights}
            busy={busy}
            canRefreshDossier={!!activeProjectId}
            onOpenPath={onOpenPath}
            onRunLint={() => void runLint()}
            onRefreshDossier={() => void refreshDossier()}
          />
        )}
      </div>
    </div>
  );
}
