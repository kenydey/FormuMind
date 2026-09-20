import type {
  WikiPageGraphBrokenLink,
  WikiPageGraphInsightPage,
  WikiPageGraphInsights,
} from "../../api";

type Props = {
  insights: WikiPageGraphInsights | null;
  busy: string | null;
  canRefreshDossier: boolean;
  onOpenPath: (path: string) => void;
  onRunLint: () => void;
  onRefreshDossier: () => void;
};

/** P1 gap sidebar — orphans / broken → open / lint / dossier refresh. */
export default function HubWikiGraphInsights({
  insights,
  busy,
  canRefreshDossier,
  onOpenPath,
  onRunLint,
  onRefreshDossier,
}: Props) {
  const orphans = insights?.orphans ?? [];
  const broken = insights?.broken ?? [];
  const isolates = insights?.isolates ?? [];
  const comps = insights?.components;

  return (
    <aside
      className="flex flex-col gap-2 min-h-0 overflow-y-auto border border-edge/50 rounded p-2 text-xs bg-ink/30"
      data-testid="hub-wiki-graph-insights"
    >
      <div className="flex items-center justify-between gap-1 shrink-0">
        <h3 className="text-[11px] text-slate-200 font-medium">缺口洞察</h3>
        {comps && (
          <span className="text-[10px] text-slate-500" title="弱连通分量">
            分量 {comps.count} · 最大 {comps.largest}
          </span>
        )}
      </div>

      <div className="flex flex-wrap gap-1 shrink-0">
        <button
          type="button"
          className="px-1.5 py-0.5 border border-amber-500/50 rounded text-amber-200 disabled:opacity-40"
          disabled={!!busy}
          data-testid="hub-wiki-graph-run-lint"
          title="扫描 stale / conflict / orphan 并写回 flags"
          onClick={onRunLint}
        >
          {busy === "lint" ? "Lint…" : "跑 Lint"}
        </button>
        <button
          type="button"
          className="px-1.5 py-0.5 border border-accent/50 rounded text-accent disabled:opacity-40"
          disabled={!!busy || !canRefreshDossier}
          data-testid="hub-wiki-graph-refresh-dossier"
          title={
            canRefreshDossier
              ? "从 live pack 刷新当前项目卷宗"
              : "需活动项目"
          }
          onClick={onRefreshDossier}
        >
          {busy === "dossier" ? "卷宗…" : "刷新卷宗"}
        </button>
      </div>

      <section data-testid="hub-wiki-graph-orphans">
        <div className="text-[10px] text-slate-400 mb-1">
          孤立（无入链 · 对齐 Lint）· {orphans.length}
          {isolates.length !== orphans.length
            ? ` · 零边 ${isolates.length}`
            : ""}
        </div>
        {orphans.length === 0 ? (
          <p className="text-[10px] text-slate-500">暂无孤立页</p>
        ) : (
          <ul className="space-y-1 max-h-36 overflow-y-auto">
            {orphans.map((o: WikiPageGraphInsightPage) => (
              <li
                key={o.path}
                className="flex items-start justify-between gap-1 border border-edge/40 rounded px-1.5 py-1"
                data-testid={`hub-wiki-graph-orphan-${o.path}`}
              >
                <div className="min-w-0">
                  <div className="text-slate-200 truncate">{o.label}</div>
                  <div className="text-[9px] text-slate-500 truncate">
                    {o.kind} · {o.path}
                  </div>
                </div>
                <button
                  type="button"
                  className="shrink-0 text-[10px] px-1 border border-edge rounded text-slate-300 hover:border-accent/50"
                  data-testid={`hub-wiki-graph-orphan-open-${o.path}`}
                  onClick={() => onOpenPath(o.path)}
                >
                  打开
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section data-testid="hub-wiki-graph-broken">
        <div className="text-[10px] text-slate-400 mb-1">
          断链样例 · {broken.length}
        </div>
        {broken.length === 0 ? (
          <p className="text-[10px] text-slate-500">暂无断链</p>
        ) : (
          <ul className="space-y-1 max-h-36 overflow-y-auto">
            {broken.map((b: WikiPageGraphBrokenLink, idx: number) => (
              <li
                key={`${b.source}->${b.target}-${idx}`}
                className="flex items-start justify-between gap-1 border border-rose-500/20 rounded px-1.5 py-1"
                data-testid={`hub-wiki-graph-broken-${idx}`}
              >
                <div className="min-w-0">
                  <div className="text-rose-200/90 truncate">
                    [[{b.target}]]
                  </div>
                  <div className="text-[9px] text-slate-500 truncate">
                    自 {b.source_label || b.source}
                    {b.reason ? ` · ${b.reason}` : ""}
                  </div>
                </div>
                <button
                  type="button"
                  className="shrink-0 text-[10px] px-1 border border-edge rounded text-slate-300 hover:border-accent/50"
                  data-testid={`hub-wiki-graph-broken-open-${idx}`}
                  onClick={() => onOpenPath(b.source)}
                >
                  打开源
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  );
}
