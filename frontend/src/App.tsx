import { useEffect } from "react";
import SourcesPanel from "./components/SourcesPanel";
import ResearchPanel from "./components/ResearchPanel";
import ActionsPanel from "./components/ActionsPanel";
import HistoryPanel from "./components/HistoryPanel";
import SettingsModal from "./components/SettingsModal";
import DegradedBanner from "./components/DegradedBanner";
import { useStore } from "./store";

function GearIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function ClockIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
    </svg>
  );
}

export default function App() {
  const { toggleHistory, toggleSettings, projects, initProjects, hydrateLlmSettings } = useStore();

  useEffect(() => {
    hydrateLlmSettings();
    void initProjects();
  }, [hydrateLlmSettings, initProjects]);

  return (
    <div className="h-screen flex flex-col bg-ink text-slate-300">
      <header className="px-5 py-3 border-b border-edge flex items-center gap-3 shrink-0">
        <div className="w-2.5 h-2.5 rounded-full bg-accent shadow-[0_0_12px_#38bdf8]" />
        <h1 className="font-semibold tracking-tight text-slate-100">
          FormuMind <span className="text-slate-500 font-normal text-sm">· Chemical Development Platform</span>
        </h1>
        <span className="ml-auto text-xs text-slate-500 font-mono hidden sm:block">自由 ProjectSpec · 多目标 · DOE 闭环</span>
        <button
          onClick={toggleSettings}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-accent border border-edge hover:border-accent/40 rounded px-2.5 py-1.5 transition-colors"
          title="设置"
        >
          <GearIcon />
          <span>设置</span>
        </button>
        <button
          onClick={toggleHistory}
          className="relative flex items-center gap-1.5 text-xs text-slate-400 hover:text-accent border border-edge hover:border-accent/40 rounded px-2.5 py-1.5 transition-colors"
          title="项目历史"
        >
          <ClockIcon />
          <span>历史</span>
          {projects.length > 0 && (
            <span className="absolute -top-1 -right-1 text-[9px] bg-accent text-ink rounded-full w-4 h-4 flex items-center justify-center font-mono">
              {projects.length > 9 ? "9+" : projects.length}
            </span>
          )}
        </button>
      </header>

      <DegradedBanner />

      <main className="flex-1 grid grid-cols-12 gap-3 p-3 overflow-hidden min-h-0">
        <div className="col-span-3 min-h-0">
          <SourcesPanel />
        </div>
        <div className="col-span-5 min-h-0">
          <ResearchPanel />
        </div>
        <div className="col-span-4 min-h-0">
          <ActionsPanel />
        </div>
      </main>

      <HistoryPanel />
      <SettingsModal />
    </div>
  );
}
