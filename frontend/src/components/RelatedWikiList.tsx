import { useEffect, useMemo, useState } from "react";
import { api, formatApiError, type Evidence } from "../api";
import WikiMarkdownReader from "./WikiMarkdownReader";
import { relatedWikiFromCitations } from "../wiki/wikiEvidence";

type Props = {
  citations?: Evidence[];
};

/** S1: related Wiki title list → open shared Reader drawer. */
export default function RelatedWikiList({ citations }: Props) {
  const related = useMemo(() => relatedWikiFromCitations(citations), [citations]);
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<{
    path: string;
    title: string;
    kind?: string;
    flags?: string[];
    source_ids?: string[];
    markdown: string;
    norm_key?: string;
    updated_at?: string | null;
  } | null>(null);

  useEffect(() => {
    if (!openPath) {
      setPage(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getWikiByPath(openPath)
      .then((d) => {
        if (cancelled) return;
        setPage({
          path: d.path,
          title: d.title,
          kind: d.kind,
          flags: d.flags,
          source_ids: d.source_ids,
          markdown: d.markdown,
          norm_key: d.norm_key,
          updated_at: d.updated_at,
        });
      })
      .catch((e) => {
        if (!cancelled) setError(formatApiError(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [openPath]);

  if (related.length === 0) return null;

  return (
    <>
      <div className="mt-2 pt-2 border-t border-edge/40" data-testid="related-wiki-list">
        <div className="text-[10px] text-slate-500 mb-1">相关 Wiki</div>
        <div className="flex flex-wrap gap-1">
          {related.map((w) => (
            <button
              key={w.path}
              type="button"
              className="text-[10px] px-1.5 py-0.5 rounded border border-violet-500/40 bg-violet-500/10 text-violet-200 hover:bg-violet-500/20"
              title={w.path}
              onClick={() => setOpenPath(w.path)}
            >
              [{w.kind}] {w.title}
            </button>
          ))}
        </div>
      </div>

      {openPath && (
        <div
          className="fixed inset-0 z-40 flex justify-end bg-black/40"
          data-testid="wiki-reader-drawer"
          onClick={() => setOpenPath(null)}
        >
          <div
            className="w-full max-w-lg h-full bg-panel border-l border-edge shadow-xl flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-3 py-2 border-b border-edge shrink-0">
              <span className="text-xs text-slate-300">Wiki 阅读</span>
              <button
                type="button"
                className="text-slate-500 hover:text-slate-300 text-sm"
                onClick={() => setOpenPath(null)}
              >
                ✕
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3 min-h-0">
              {loading && <div className="text-xs text-slate-500">加载中…</div>}
              {error && (
                <div className="text-xs text-rose-300 border border-rose-500/40 rounded px-2 py-1">
                  {error}
                </div>
              )}
              {page && !loading && (
                <WikiMarkdownReader
                  page={page}
                  linkPages={related.map((r) => ({
                    path: r.path,
                    title: r.title,
                    kind: r.kind,
                  }))}
                  onNavigatePath={(path) => setOpenPath(path)}
                />
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
