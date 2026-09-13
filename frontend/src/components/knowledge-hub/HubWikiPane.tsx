import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  formatApiError,
  type WikiPageDetail,
  type WikiPageItem,
} from "../../api";
import WikiMarkdownReader from "../WikiMarkdownReader";

/** Wiki pane inside Knowledge Hub (read-only). S1: Reader + substring search + wikilinks. */
export default function HubWikiPane({ active }: { active: boolean }) {
  const [pages, setPages] = useState<WikiPageItem[]>([]);
  const [kind, setKind] = useState("");
  const [detail, setDetail] = useState<WikiPageDetail | null>(null);
  const [flagsOnly, setFlagsOnly] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (flagsOnly) {
        const r = await api.listWikiFlags({ limit: 100 });
        setPages(
          r.pages.map((p) => ({
            id: p.id,
            path: p.path,
            kind: p.kind,
            title: p.title,
            norm_key: "",
            source_ids: p.source_ids,
            flags: p.flags,
            revision: 1,
            updated_at: null,
          })),
        );
      } else {
        const r = await api.listWikiPages({ kind: kind || undefined, limit: 100 });
        setPages(r.pages ?? []);
      }
    } catch (e) {
      setPages([]);
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [flagsOnly, kind]);

  useEffect(() => {
    if (active) void refresh();
  }, [active, refresh]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return pages;
    return pages.filter((p) => {
      const title = (p.title || "").toLowerCase();
      const path = (p.path || "").toLowerCase();
      const nk = (p.norm_key || "").toLowerCase();
      return title.includes(q) || path.includes(q) || nk.includes(q);
    });
  }, [pages, query]);

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
    } catch (e) {
      setError(formatApiError(e));
    }
  }, []);

  return (
    <div className="flex flex-col gap-2 h-full min-h-0" data-testid="hub-wiki-pane">
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
        </select>
        <label className="flex items-center gap-1 text-slate-400">
          <input
            type="checkbox"
            checked={flagsOnly}
            onChange={(e) => setFlagsOnly(e.target.checked)}
          />
          仅 Flag
        </label>
        <input
          type="search"
          className="bg-ink border border-edge rounded px-2 py-1 text-slate-200 min-w-[10rem] flex-1"
          placeholder="搜索标题 / path…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="hub-wiki-search"
        />
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded"
          onClick={() => void refresh()}
        >
          刷新
        </button>
      </div>
      {error && (
        <div className="text-xs text-rose-300 border border-rose-500/40 rounded px-2 py-1">
          {error}
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
            <li key={p.id}>
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
              </button>
            </li>
          ))}
        </ul>
        <div className="overflow-y-auto border border-edge/60 rounded p-3 min-h-0">
          {detail ? (
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
          ) : (
            <span className="text-slate-500 text-xs">选择左侧页面查看编译记忆</span>
          )}
        </div>
      </div>
    </div>
  );
}
