import RequirementPanel from "./components/RequirementPanel";
import ChatPanel from "./components/ChatPanel";
import SimPlaceholder from "./components/SimPlaceholder";
import FormulaLeaderboard from "./components/FormulaLeaderboard";
import DoeResultsPanel from "./components/DoeResultsPanel";
import HistoryPanel from "./components/HistoryPanel";
import { useStore } from "./store";

function ClockIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}

export default function App() {
  const { toggleHistory, history } = useStore();

  return (
    <div className="h-screen flex flex-col bg-ink text-slate-300">
      <header className="px-5 py-3 border-b border-edge flex items-center gap-3">
        <div className="w-2.5 h-2.5 rounded-full bg-accent shadow-[0_0_12px_#38bdf8]" />
        <h1 className="font-semibold tracking-tight text-slate-100">
          FormuMind <span className="text-slate-500 font-normal text-sm">· 金属表面处理配方研发平台</span>
        </h1>
        <span className="ml-auto text-xs text-slate-500 font-mono">脱脂 · 表面处理 · 防腐涂料</span>
        <button
          onClick={toggleHistory}
          className="relative flex items-center gap-1.5 text-xs text-slate-400 hover:text-accent border border-edge hover:border-accent/40 rounded px-2.5 py-1.5 transition-colors"
          title="历史会话"
        >
          <ClockIcon />
          <span>历史</span>
          {history.length > 0 && (
            <span className="absolute -top-1 -right-1 text-[9px] bg-accent text-ink rounded-full w-4 h-4 flex items-center justify-center font-mono">
              {history.length > 9 ? "9+" : history.length}
            </span>
          )}
        </button>
      </header>

      <main className="flex-1 grid grid-cols-12 gap-3 p-3 overflow-hidden">
        {/* Left: requirement input */}
        <div className="col-span-3 min-h-0">
          <RequirementPanel />
        </div>

        {/* Centre: AI research stream (top) + DOE feedback loop (bottom) */}
        <div className="col-span-5 min-h-0 grid grid-rows-2 gap-3">
          <ChatPanel />
          <DoeResultsPanel />
        </div>

        {/* Right: convergence chart (top) + leaderboard (bottom) */}
        <div className="col-span-4 min-h-0 grid grid-rows-2 gap-3">
          <SimPlaceholder />
          <FormulaLeaderboard />
        </div>
      </main>

      <HistoryPanel />
    </div>
  );
}
