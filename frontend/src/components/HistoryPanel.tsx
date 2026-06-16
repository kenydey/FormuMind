import { useStore, type SessionSnapshot } from "../store";

const DOMAIN_LABEL: Record<string, string> = {
  anticorrosion_coating: "防腐涂料",
  degreaser: "脱脂剂",
  surface_treatment: "表面处理",
};

function fmt(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function SnapCard({ snap, onRestore }: { snap: SessionSnapshot; onRestore: () => void }) {
  return (
    <button
      onClick={onRestore}
      className="w-full text-left border border-edge/50 rounded-lg p-3 bg-ink/60 hover:border-accent/40 hover:bg-accent/5 transition-colors group"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="text-[10px] uppercase tracking-widest text-accent2 font-semibold">
              {DOMAIN_LABEL[snap.domain] ?? snap.domain}
            </span>
            <span className="text-[10px] text-slate-600">·</span>
            <span className="text-[10px] text-slate-500 font-mono">{fmt(snap.timestamp)}</span>
          </div>
          <p className="text-xs text-slate-300 truncate">{snap.headline}</p>
          {snap.leaderboard.length > 0 && (
            <p className="text-[10px] text-slate-500 mt-0.5 truncate">
              Top 配方: {snap.leaderboard[0].name}
            </p>
          )}
        </div>
        <div className="shrink-0 text-right">
          {snap.topScore != null && (
            <span className="text-sm font-mono text-accent group-hover:text-accent">
              {snap.topScore.toFixed(2)}
            </span>
          )}
          <div className="text-[9px] text-slate-600 mt-0.5">{snap.leaderboard.length} 配方</div>
        </div>
      </div>
    </button>
  );
}

export default function HistoryPanel() {
  const { historyOpen, history, toggleHistory, restoreSnapshot, clearHistory } = useStore();

  if (!historyOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-30 bg-ink/60 backdrop-blur-sm"
        onClick={toggleHistory}
      />
      {/* Drawer */}
      <div className="fixed right-0 top-0 h-full w-80 z-40 bg-panel border-l border-edge flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-edge">
          <h2 className="text-sm font-semibold text-slate-200">历史会话</h2>
          <div className="flex items-center gap-2">
            {history.length > 0 && (
              <button
                onClick={clearHistory}
                className="text-[10px] text-slate-500 hover:text-red-400 px-1.5 py-0.5 rounded border border-edge hover:border-red-500/40"
              >
                清空
              </button>
            )}
            <button
              onClick={toggleHistory}
              className="text-slate-400 hover:text-slate-200 w-6 h-6 flex items-center justify-center rounded hover:bg-edge"
              aria-label="关闭历史面板"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {history.length === 0 ? (
            <p className="text-slate-500 text-sm text-center mt-8">
              暂无历史会话。<br />
              <span className="text-xs">完成研究或寻优后将自动保存。</span>
            </p>
          ) : (
            history.map((snap) => (
              <SnapCard
                key={snap.id}
                snap={snap}
                onRestore={() => restoreSnapshot(snap)}
              />
            ))
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t border-edge text-[10px] text-slate-600">
          最多保存 {20} 条 · 数据存储于浏览器 localStorage
        </div>
      </div>
    </>
  );
}
