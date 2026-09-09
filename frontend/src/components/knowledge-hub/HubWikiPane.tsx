import { useCallback, useEffect, useState } from "react";
import { api, formatApiError, type WikiPageDetail, type WikiPageItem } from "../../api";

/** Wiki pane inside Knowledge Hub (read-only). */
export default function HubWikiPane({ active }: { active: boolean }) {
  const [pages, setPages] = useState<WikiPageItem[]>([]);
  const [kind, setKind] = useState("");
  const [detail, setDetail] = useState<WikiPageDetail | null>(null);
  const [flagsOnly, setFlagsOnly] = useState(false);
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
          <input type="checkbox" checked={flagsOnly} onChange={(e) => setFlagsOnly(e.target.checked)} />
          仅 Flag
        </label>
        <button
          type="button"
          className="px-2 py-1 border border-edge rounded"
          onClick={() => void refresh()}
        >
          刷新
        </button>
      </div>
      {error && (
        <div className="text-xs text-rose-300 border border-rose-500/40 rounded px-2 py-1">{error}</div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 min-h-0 flex-1 overflow-hidden">
        <ul className="overflow-y-auto border border-edge/60 rounded divide-y divide-edge/40 text-sm">
          {loading && <li className="px-3 py-2 text-slate-500">加载中…</li>}
          {!loading && pages.length === 0 && (
            <li className="px-3 py-2 text-slate-500">暂无 Wiki 页（入库后自动编译）</li>
          )}
          {pages.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className="w-full text-left px-3 py-2 hover:bg-accent/5"
                onClick={async () => {
                  try {
                    setDetail(await api.getWikiByPath(p.path));
                  } catch (e) {
                    setError(formatApiError(e));
                  }
                }}
              >
                <div className="text-slate-200 truncate">{p.title || p.path}</div>
                <div className="text-[10px] text-slate-500 flex gap-2 flex-wrap">
                  <span>{p.kind}</span>
                  <code>{p.path}</code>
                  {(p.flags || []).map((f) => (
                    <span key={f} className="text-amber-300 border border-amber-500/40 rounded px-1">
                      {f}
                    </span>
                  ))}
                </div>
              </button>
            </li>
          ))}
        </ul>
        <div className="overflow-y-auto border border-edge/60 rounded p-3 text-xs text-slate-300 whitespace-pre-wrap font-mono">
          {detail ? (
            <>
              <div className="text-sm text-slate-100 mb-2 font-sans">{detail.title}</div>
              <div className="text-[10px] text-slate-500 mb-2 font-sans">{detail.path}</div>
              {detail.markdown || "_empty_"}
            </>
          ) : (
            <span className="text-slate-500 font-sans">选择左侧页面查看 Markdown</span>
          )}
        </div>
      </div>
    </div>
  );
}
