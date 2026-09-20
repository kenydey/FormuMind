import { useCallback, useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  api,
  formatApiError,
  type WikiFlagAction,
  type WikiPageDetail,
  type WikiPageItem,
  type WikiSearchHit,
} from "../../api";
import { useStore } from "../../store";
import WikiMarkdownReader from "../WikiMarkdownReader";
import HubWikiGraphPane from "./HubWikiGraphPane";

type DossierMeta = {
  section_revisions?: Record<string, number>;
  flags?: Record<string, boolean>;
  project_id?: string;
  template?: string;
};

type FlagPageItem = WikiPageItem & { actions?: WikiFlagAction[] };

/** Wiki pane inside Knowledge Hub (read-only). S1 reader + P2 FTS search + P4 dossier. */
export default function HubWikiPane({ active }: { active: boolean }) {
  const activeProjectId = useStore(useShallow((s) => s.activeProjectId));
  const [pages, setPages] = useState<FlagPageItem[]>([]);
  const [kind, setKind] = useState("");
  const [detail, setDetail] = useState<WikiPageDetail | null>(null);
  const [dossierMeta, setDossierMeta] = useState<DossierMeta | null>(null);
  const [flagsOnly, setFlagsOnly] = useState(false);
  const [reviewedOnly, setReviewedOnly] = useState(false);
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<WikiSearchHit[] | null>(null);
  const [searchMode, setSearchMode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lintSummary, setLintSummary] = useState<string | null>(null);

  const [busy, setBusy] = useState<string | null>(null);
  /** list = classic browser; graph = [[wikilink]] canvas (P0). */
  const [viewMode, setViewMode] = useState<"list" | "graph">("list");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    if (!activeProjectId) {
      setPages([]);
      setError("请先选择活动项目：Wiki 仅显示当前项目关联页（卷宗/报告/本项目资料编译页）");
      setLoading(false);
      return;
    }
    try {
      if (flagsOnly) {
        const r = await api.listWikiFlags({ limit: 100, project_id: activeProjectId });
        setPages(
          r.pages.map((p) => ({
            id: p.id,
            path: p.path,
            kind: p.kind,
            title: p.title,
            norm_key: p.norm_key || "",
            source_ids: p.source_ids,
            flags: p.flags,
            revision: 1,
            updated_at: null,
            actions: p.actions || [],
          })),
        );
      } else {
        const r = await api.listWikiPages({
          kind: kind || undefined,
          limit: 100,
          project_id: activeProjectId,
        });
        setPages(r.pages ?? []);
      }
    } catch (e) {
      setPages([]);
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [flagsOnly, kind, activeProjectId]);

  const runOps = useCallback(
    async (label: string, fn: () => Promise<unknown>) => {
      setBusy(label);
      setError(null);
      try {
        await fn();
        await refresh();
        if (detail?.path) {
          setDetail(await api.getWikiByPath(detail.path));
        }
      } catch (e) {
        setError(formatApiError(e));
      } finally {
        setBusy(null);
      }
    },
    [detail?.path, refresh],
  );

  useEffect(() => {
    if (active) void refresh();
  }, [active, refresh]);

  // P2: server FTS when query length >= 2 and not flags-only
  useEffect(() => {
    if (!active || flagsOnly) {
      setSearchHits(null);
      setSearchMode(null);
      return;
    }
    const q = query.trim();
    if (q.length < 2) {
      setSearchHits(null);
      setSearchMode(null);
      return;
    }
    let cancelled = false;
    const t = window.setTimeout(() => {
      api
        .searchWikiPages({
          q,
          kind: kind || undefined,
          limit: 50,
          project_id: activeProjectId || undefined,
        })
        .then((r) => {
          if (cancelled) return;
          setSearchHits(r.hits ?? []);
          setSearchMode(r.mode);
        })
        .catch(() => {
          if (!cancelled) {
            setSearchHits(null);
            setSearchMode(null);
          }
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [active, flagsOnly, query, kind, activeProjectId]);

  const filtered = useMemo(() => {
    let list = pages;
    if (reviewedOnly) {
      list = list.filter((p) => !(p.flags || []).includes("unreviewed"));
    }
    if (searchHits) {
      const byPath = new Map(list.map((p) => [p.path, p]));
      return searchHits.map((h) => {
        const existing = byPath.get(h.path);
        if (existing) return existing;
        return {
          id: h.id || h.path,
          path: h.path,
          kind: h.kind,
          title: h.title,
          norm_key: h.norm_key || "",
          source_ids: h.source_ids || [],
          flags: h.flags || [],
          revision: 1,
          updated_at: null,
        } as WikiPageItem;
      });
    }
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((p) => {
      const title = (p.title || "").toLowerCase();
      const path = (p.path || "").toLowerCase();
      const nk = (p.norm_key || "").toLowerCase();
      return title.includes(q) || path.includes(q) || nk.includes(q);
    });
  }, [pages, query, searchHits, reviewedOnly]);

  const linkPages = useMemo(
    () =>
      pages.map((p) => ({
        path: p.path,
        title: p.title || p.path,
        norm_key: p.norm_key,
        kind: p.kind,
      })),
    [pages],
  );

  const openPath = useCallback(async (path: string) => {
    try {
      setDetail(await api.getWikiByPath(path));
      setError(null);
      if (path.startsWith("themes/project-") && path.endsWith(".md")) {
        // Prefer structured sidecar via dossier API when path looks like a dossier.
        const m = path.match(/^themes\/project-(.+)\.md$/i);
        const pid = m?.[1];
        if (pid) {
          try {
            const d = await api.getWikiDossier(pid);
            setDossierMeta((d.data as DossierMeta) || null);
          } catch {
            setDossierMeta(null);
          }
        } else {
          setDossierMeta(null);
        }
      } else {
        setDossierMeta(null);
      }
    } catch (e) {
      setError(formatApiError(e));
    }
  }, []);

  const openProjectDossier = useCallback(async () => {
    if (!activeProjectId) {
      setError("请先在工作区选择/打开一个项目");
      return;
    }
    setBusy("dossier-open");
    setError(null);
    try {
      let page;
      try {
        page = await api.getWikiDossier(activeProjectId);
      } catch {
        await api.ensureWikiDossier({ project_id: activeProjectId });
        page = await api.getWikiDossier(activeProjectId);
        await refresh();
      }
      setDetail({
        id: page.page_id || page.path,
        path: page.path,
        kind: "theme",
        title: page.title || page.path,
        flags: page.flags || [],
        source_ids: [],
        markdown: page.markdown,
        revision: page.revision,
      });
      setDossierMeta((page.data as DossierMeta) || null);
      setKind("theme");
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(null);
    }
  }, [activeProjectId, refresh]);

  const refreshProjectDossier = useCallback(async () => {
    if (!activeProjectId) {
      setError("请先在工作区选择/打开一个项目");
      return;
    }
    await runOps("dossier-refresh", async () => {
      const out = await api.refreshWikiDossier({ project_id: activeProjectId });
      if (out.path) {
        const page = await api.getWikiDossier(activeProjectId);
        setDetail({
          id: page.page_id || page.path,
          kind: "theme",
          title: page.title || page.path,
          path: page.path,
          flags: page.flags || [],
          source_ids: [],
          markdown: page.markdown,
          revision: page.revision,
        });
        setDossierMeta((page.data as DossierMeta) || null);
      }
    });
  }, [activeProjectId, runOps]);

  const runFlagAction = useCallback(
    async (page: FlagPageItem, action: WikiFlagAction) => {
      const id = action.id;
      const target = (action.target || "").trim();

      if (id === "open_page") {
        await openPath(page.path);
        return;
      }
      if (id === "mark_reviewed") {
        await runOps("review", () => api.reviewWikiPage({ path: page.path, reviewed: true }));
        return;
      }
      if (id === "compile_theme") {
        const key = page.norm_key || page.path.split("/").pop()?.replace(/\.md$/i, "");
        if (!key) return;
        await runOps("theme", () => api.compileWikiTheme({ system_key: key, use_llm: false }));
        return;
      }
      if (id === "refresh_dossier") {
        const m = page.path.match(/^themes\/project-(.+)\.md$/i);
        const pid = m?.[1] || activeProjectId;
        if (!pid) {
          setError("无法从 path 解析 project_id");
          return;
        }
        await runOps("dossier-refresh", async () => {
          await api.refreshWikiDossier({ project_id: pid });
          await openPath(page.path);
        });
        return;
      }
      // S1: open candidate theme/dossier for manual [[wikilink]] (never auto-edit L1)
      if (id === "link_from_theme" || id.startsWith("open_link_candidate")) {
        const openTo = target || page.path;
        setLintSummary(`${action.label}：${action.hint || openTo}`);
        await openPath(openTo);
        return;
      }
      // Soft guidance — open target (page) and surface hint
      setLintSummary(`${action.label}：${action.hint || page.path}`);
      await openPath(target || page.path);
    },
    [activeProjectId, openPath, runOps],
  );

  const hitSnippet = (path: string) =>
    searchHits?.find((h) => h.path === path)?.snippet || null;

  const isDossier =
    !!detail?.path?.startsWith("themes/project-") && detail.path.endsWith(".md");

  const revisionEntries = useMemo(() => {
    const rev = dossierMeta?.section_revisions || {};
    return Object.entries(rev).sort(([a], [b]) => a.localeCompare(b));
  }, [dossierMeta]);

  const packFlags = dossierMeta?.flags || {};

  return (
    <div className="flex flex-col gap-2 h-full min-h-0" data-testid="hub-wiki-pane">
      <div
        className="text-[10px] text-slate-400 border border-edge/50 rounded px-2 py-1"
        data-testid="hub-wiki-project-scope"
      >
        {activeProjectId
          ? `仅显示当前项目 Wiki · project_id=${activeProjectId}（卷宗/报告 + 本项目资料编译页）`
          : "未选择活动项目 — Wiki 按项目隔离，请先打开/选择项目"}
      </div>
      <div
        className="flex items-center gap-1 text-xs shrink-0"
        data-testid="hub-wiki-view-toggle"
        role="tablist"
        aria-label="Wiki 视图"
      >
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === "list"}
          className={`px-2 py-1 border rounded ${
            viewMode === "list"
              ? "border-accent/60 text-accent bg-accent/10"
              : "border-edge text-slate-400"
          }`}
          data-testid="hub-wiki-view-list"
          onClick={() => setViewMode("list")}
        >
          列表
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === "graph"}
          className={`px-2 py-1 border rounded ${
            viewMode === "graph"
              ? "border-accent/60 text-accent bg-accent/10"
              : "border-edge text-slate-400"
          }`}
          data-testid="hub-wiki-view-graph"
          title="Wiki 页 [[wikilink]] 链接图（非材料 KG）"
          onClick={() => setViewMode("graph")}
        >
          链接图
        </button>
      </div>
      {viewMode === "graph" ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 min-h-0 flex-1 overflow-hidden">
          <HubWikiGraphPane
            active={active}
            selectedPath={detail?.path}
            onOpenPath={(path) => void openPath(path)}
          />
          <div className="overflow-y-auto border border-edge/60 rounded p-3 text-sm min-h-[12rem]">
            {detail ? (
              <div className="space-y-2">
                <div className="text-[10px] text-slate-500 mb-2 flex gap-2 flex-wrap">
                  <span>{detail.kind}</span>
                  <code>{detail.path}</code>
                  {(detail.flags || []).map((f) => (
                    <span
                      key={f}
                      className="text-amber-300 border border-amber-500/40 rounded px-1"
                    >
                      {f}
                    </span>
                  ))}
                </div>
                <WikiMarkdownReader
                  page={{
                    path: detail.path,
                    title: detail.title,
                    kind: detail.kind,
                    flags: detail.flags,
                    source_ids: detail.source_ids,
                    markdown: detail.markdown,
                    norm_key: detail.norm_key,
                    updated_at: detail.updated_at,
                  }}
                  linkPages={linkPages}
                  onNavigatePath={(path) => void openPath(path)}
                />
              </div>
            ) : (
              <p className="text-slate-500 text-xs">点击图中节点打开 Wiki 页</p>
            )}
          </div>
        </div>
      ) : (
      <>
      <div className="flex flex-wrap items-center gap-2 text-xs shrink-0">
        <select
          className="bg-ink border border-edge rounded px-2 py-1 text-slate-200"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          disabled={flagsOnly}
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
            checked={flagsOnly}
            onChange={(e) => setFlagsOnly(e.target.checked)}
          />
          仅 Flag
        </label>
        <label className="flex items-center gap-1 text-slate-400" title="隐藏 flags 含 unreviewed 的主题草稿">
          <input
            type="checkbox"
            checked={reviewedOnly}
            onChange={(e) => setReviewedOnly(e.target.checked)}
          />
          仅已审
        </label>
        <input
          type="search"
          className="bg-ink border border-edge rounded px-2 py-1 text-slate-200 min-w-[10rem] flex-1"
          placeholder="搜索标题 / path / 正文…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="hub-wiki-search"
        />
        {searchMode && query.trim().length >= 2 && (
          <span className="text-[10px] text-slate-500" title="检索后端">
            {searchMode === "fts" ? "FTS" : "关键词"}
          </span>
        )}
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded"
          onClick={() => void refresh()}
        >
          刷新
        </button>
        <button
          type="button"
          className="px-2 py-1 border border-amber-500/50 rounded text-amber-200 disabled:opacity-40"
          disabled={!!busy}
          title="扫描 stale / conflict / orphan / broken 并写回 flags（W4/S1）"
          data-testid="hub-wiki-run-lint"
          onClick={() => {
            void (async () => {
              setBusy("lint");
              setError(null);
              try {
                const out = await api.runWikiLint({ limit: 200, detect_orphan: true });
                setLintSummary(
                  `Lint：扫描 ${out.scanned ?? "?"} · 有旗标 ${out.flagged ?? "?"} · 孤儿 ${out.orphan_count ?? "?"} · 断链页 ${out.broken_count ?? "?"}`,
                );
                setFlagsOnly(true);
                const r = await api.listWikiFlags({
                  limit: 100,
                  project_id: activeProjectId || undefined,
                });
                setPages(
                  r.pages.map((p) => ({
                    id: p.id,
                    path: p.path,
                    kind: p.kind,
                    title: p.title,
                    norm_key: p.norm_key || "",
                    source_ids: p.source_ids,
                    flags: p.flags,
                    revision: 1,
                    updated_at: null,
                    actions: p.actions || [],
                  })),
                );
              } catch (e) {
                setError(formatApiError(e));
              } finally {
                setBusy(null);
              }
            })();
          }}
        >
          {busy === "lint" ? "Lint…" : "跑 Lint"}
        </button>
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded text-slate-300 disabled:opacity-40"
          disabled={!!busy}
          title="仅重扫当前有 Flag 的页，清除已过时的 lint 旗标（S1）"
          data-testid="hub-wiki-sweep-lint"
          onClick={() => {
            void (async () => {
              setBusy("sweep");
              setError(null);
              try {
                const out = await api.sweepWikiLint({ limit: 200, detect_orphan: true });
                setLintSummary(
                  `Sweep：重扫 ${out.scanned ?? "?"} · 清除 ${out.cleared ?? "?"} · 仍有旗标 ${out.still_flagged ?? "?"}`,
                );
                setFlagsOnly(true);
                const r = await api.listWikiFlags({
                  limit: 100,
                  project_id: activeProjectId || undefined,
                });
                setPages(
                  r.pages.map((p) => ({
                    id: p.id,
                    path: p.path,
                    kind: p.kind,
                    title: p.title,
                    norm_key: p.norm_key || "",
                    source_ids: p.source_ids,
                    flags: p.flags,
                    revision: 1,
                    updated_at: null,
                    actions: p.actions || [],
                  })),
                );
              } catch (e) {
                setError(formatApiError(e));
              } finally {
                setBusy(null);
              }
            })();
          }}
        >
          {busy === "sweep" ? "Sweep…" : "清过期 Flag"}
        </button>
        <button
          type="button"
          className="px-2 py-1 border border-accent/50 rounded text-accent disabled:opacity-40"
          disabled={!!busy || !activeProjectId}
          title={
            activeProjectId
              ? `打开/生成当前项目卷宗（project_id=${activeProjectId}）`
              : "需先选择活动项目"
          }
          data-testid="hub-wiki-open-dossier"
          onClick={() => void openProjectDossier()}
        >
          {busy === "dossier-open" ? "卷宗…" : "项目卷宗"}
        </button>
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded disabled:opacity-40"
          disabled={!!busy || !activeProjectId}
          title="从 live pack 刷新卷宗各节表（确定性；LLM 叙述默认关）"
          data-testid="hub-wiki-refresh-dossier"
          onClick={() => void refreshProjectDossier()}
        >
          {busy === "dossier-refresh" ? "刷新卷宗…" : "刷新卷宗"}
        </button>
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded disabled:opacity-40"
          disabled={!!busy}
          title="重建 Wiki FTS 索引（需 wiki_fts_enabled）"
          data-testid="hub-wiki-rebuild-fts"
          onClick={() => void runOps("fts", () => api.rebuildWikiFts())}
        >
          {busy === "fts" ? "FTS…" : "重建 FTS"}
        </button>
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded disabled:opacity-40"
          disabled={!!busy}
          title="重建确定性 catalog.md（从 wiki_pages；App 维护，禁止 LLM 覆盖）"
          data-testid="hub-wiki-rebuild-catalog"
          onClick={() => {
            void (async () => {
              setBusy("catalog");
              setError(null);
              try {
                const out = await api.rebuildWikiCatalog({
                  persist: true,
                  limit: 500,
                  project_id: activeProjectId || undefined,
                });
                setLintSummary(
                  `Catalog：条目 ${out.entry_count ?? "?"} · ${out.persisted ? "已写 catalog.md" : "未落盘"}`,
                );
              } catch (e) {
                setError(formatApiError(e));
              } finally {
                setBusy(null);
              }
            })();
          }}
        >
          {busy === "catalog" ? "Catalog…" : "重建 Catalog"}
        </button>
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded disabled:opacity-40"
          disabled={!!busy}
          title="下载 catalog.md（确定性目录，供 L2 导航）"
          data-testid="hub-wiki-download-catalog"
          onClick={() => {
            void (async () => {
              setBusy("catalog-dl");
              setError(null);
              try {
                const { blob, filename } = await api.downloadWikiCatalogMd({
                  limit: 500,
                  project_id: activeProjectId || undefined,
                });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                setLintSummary(`已下载 ${filename}`);
              } catch (e) {
                setError(formatApiError(e));
              } finally {
                setBusy(null);
              }
            })();
          }}
        >
          {busy === "catalog-dl" ? "下载…" : "下载 Catalog"}
        </button>
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded disabled:opacity-40"
          disabled={!!busy}
          title="重建 Wiki 摘要向量索引（需 wiki_embed_enabled）"
          data-testid="hub-wiki-rebuild-embed"
          onClick={() => void runOps("embed", () => api.rebuildWikiEmbed())}
        >
          {busy === "embed" ? "Embed…" : "重建 Embed"}
        </button>
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded disabled:opacity-40"
          disabled={!!busy || detail?.kind !== "system"}
          title="编译当前体系的 L2 综述（需 wiki_llm_themes_enabled）"
          data-testid="hub-wiki-compile-theme"
          onClick={() => {
            const key = detail?.norm_key || detail?.path?.split("/").pop()?.replace(/\.md$/i, "");
            if (!key) return;
            void runOps("theme", () =>
              api.compileWikiTheme({ system_key: key, use_llm: false }),
            );
          }}
        >
          {busy === "theme" ? "主题…" : "编译主题"}
        </button>
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded disabled:opacity-40"
          disabled={!!busy || !detail}
          title="Q4 预留：标记当前页已审（非完整编辑器）"
          data-testid="hub-wiki-mark-reviewed"
          onClick={() => {
            if (!detail) return;
            void runOps("review", () =>
              api.reviewWikiPage({ path: detail.path, reviewed: true }),
            );
          }}
        >
          {busy === "review" ? "审阅…" : "标记已审"}
        </button>
      </div>
      {error && (
        <div className="text-xs text-rose-300 border border-rose-500/40 rounded px-2 py-1">
          {error}
        </div>
      )}
      {lintSummary && (
        <div
          className="text-[10px] text-amber-200/90 border border-amber-500/30 rounded px-2 py-1"
          data-testid="hub-wiki-lint-summary"
        >
          {lintSummary}
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 min-h-0 flex-1 overflow-hidden">
        <ul className="overflow-y-auto border border-edge/60 rounded divide-y divide-edge/40 text-sm">
          {loading && <li className="px-3 py-2 text-slate-500">加载中…</li>}
          {!loading && filtered.length === 0 && (
            <li className="px-3 py-2 text-slate-500">
              {pages.length === 0 ? "暂无 Wiki 页（入库后自动编译）" : "无匹配结果"}
            </li>
          )}
          {filtered.map((p) => (
            <li key={p.id || p.path} data-testid={`hub-wiki-flag-row-${p.path}`}>
              <button
                type="button"
                className={`w-full text-left px-3 py-2 hover:bg-accent/5 ${
                  detail?.path === p.path ? "bg-accent/10" : ""
                }`}
                onClick={() => void openPath(p.path)}
              >
                <div className="text-slate-200 truncate">{p.title || p.path}</div>
                <div className="text-[10px] text-slate-500 flex gap-2 flex-wrap">
                  <span>{p.kind}</span>
                  <code>{p.path}</code>
                  {(p.flags || []).map((f) => (
                    <span
                      key={f}
                      className="text-amber-300 border border-amber-500/40 rounded px-1"
                    >
                      {f}
                    </span>
                  ))}
                </div>
                {hitSnippet(p.path) && (
                  <div className="text-[10px] text-slate-400 mt-0.5 line-clamp-2">
                    {hitSnippet(p.path)}
                  </div>
                )}
              </button>
              {flagsOnly && (p as FlagPageItem).actions && (p as FlagPageItem).actions!.length > 0 && (
                <div
                  className="px-3 pb-2 flex flex-wrap gap-1"
                  data-testid={`hub-wiki-flag-actions-${p.path}`}
                >
                  {(p as FlagPageItem).actions!.map((a) => (
                    <button
                      key={a.id}
                      type="button"
                      className="text-[10px] px-1.5 py-0.5 border border-edge/70 rounded text-slate-300 hover:border-accent/50 hover:text-accent disabled:opacity-40"
                      title={a.hint || a.label}
                      disabled={!!busy}
                      data-testid={`hub-wiki-flag-action-${a.id}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        void runFlagAction(p as FlagPageItem, a);
                      }}
                    >
                      {a.label}
                    </button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
        <div className="overflow-y-auto border border-edge/60 rounded p-3 min-h-0">
          {detail ? (
            <div className="space-y-2">
              {isDossier && (
                <div
                  className="text-[10px] text-slate-400 border border-edge/50 rounded px-2 py-1.5 space-y-1"
                  data-testid="hub-wiki-dossier-meta"
                >
                  <div className="text-slate-300">
                    项目卷宗 · section_revisions
                    {(detail.flags || []).includes("unreviewed") && (
                      <span className="ml-2 text-amber-300 border border-amber-500/40 rounded px-1">
                        未审
                      </span>
                    )}
                  </div>
                  {revisionEntries.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {revisionEntries.map(([k, v]) => (
                        <code key={k} className="bg-ink/60 border border-edge/40 rounded px-1">
                          {k}:{v}
                        </code>
                      ))}
                    </div>
                  ) : (
                    <div>尚无 sidecar revisions</div>
                  )}
                  {Object.keys(packFlags).length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(packFlags)
                        .filter(([, v]) => !!v)
                        .map(([k]) => (
                          <span
                            key={k}
                            className="text-amber-300/90 border border-amber-500/30 rounded px-1"
                          >
                            {k}
                          </span>
                        ))}
                    </div>
                  )}
                </div>
              )}
              <WikiMarkdownReader
                page={{
                  path: detail.path,
                  title: detail.title,
                  kind: detail.kind,
                  flags: detail.flags,
                  source_ids: detail.source_ids,
                  markdown: detail.markdown,
                  norm_key: detail.norm_key,
                  updated_at: detail.updated_at,
                }}
                linkPages={linkPages}
                onNavigatePath={(path) => void openPath(path)}
              />
            </div>
          ) : (
            <span className="text-slate-500 text-xs">
              选择左侧页面查看编译记忆；或点「项目卷宗」打开当前活动项目的 Dossier
            </span>
          )}
        </div>
      </div>
      </>
      )}
    </div>
  );
}
