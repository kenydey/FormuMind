import RequirementPanel from "./components/RequirementPanel";
import ChatPanel from "./components/ChatPanel";
import SimPlaceholder from "./components/SimPlaceholder";
import FormulaLeaderboard from "./components/FormulaLeaderboard";

export default function App() {
  return (
    <div className="h-screen flex flex-col bg-ink text-slate-300">
      <header className="px-5 py-3 border-b border-edge flex items-center gap-3">
        <div className="w-2.5 h-2.5 rounded-full bg-accent shadow-[0_0_12px_#38bdf8]" />
        <h1 className="font-semibold tracking-tight text-slate-100">
          FormuMind <span className="text-slate-500 font-normal text-sm">· 金属表面处理配方研发平台</span>
        </h1>
        <span className="ml-auto text-xs text-slate-500 font-mono">脱脂 · 表面处理 · 防腐涂料</span>
      </header>

      <main className="flex-1 grid grid-cols-12 gap-3 p-3 overflow-hidden">
        {/* Left: requirement input */}
        <div className="col-span-3 min-h-0">
          <RequirementPanel />
        </div>

        {/* Centre: AI research / chat stream */}
        <div className="col-span-5 min-h-0">
          <ChatPanel />
        </div>

        {/* Right: 3D sim (top) + leaderboard (bottom) */}
        <div className="col-span-4 min-h-0 grid grid-rows-2 gap-3">
          <SimPlaceholder />
          <FormulaLeaderboard />
        </div>
      </main>
    </div>
  );
}
