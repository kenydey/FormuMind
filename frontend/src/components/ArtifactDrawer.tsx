import { useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  selectProjectArtifacts,
  type ArtifactKind,
  type ProjectArtifact,
} from "../artifacts/projectArtifacts";
import { useStore } from "../store";

const KIND_ICON: Record<ArtifactKind, string> = {
  leaderboard: "⭐",
  doe_plan: "🔬",
  optimization: "📈",
  deep_report: "📑",
  loop_report: "🔄",
};

function ArtifactCard({
  artifact,
  active,
  onOpen,
}: {
  artifact: ProjectArtifact;
  active: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid={`artifact-card-${artifact.id}`}
      className={`w-full text-left border rounded-lg p-3 transition-colors ${
        active
          ? "border-accent/50 bg-accent/10"
          : "border-edge/50 bg-ink/60 hover:border-accent/40 hover:bg-accent/5"
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-base" aria-hidden>
          {KIND_ICON[artifact.kind]}
        </span>
        <span className="text-sm font-semibold text-slate-200 truncate">{artifact.title}</span>
        <span className="ml-auto shrink-0">
          {artifact.busy ? (
            <span className="text-[10px] font-mono rounded-full border px-1.5 py-0.5 bg-amber-500/20 text-amber-400 border-amber-500/40">
              进行中
            </span>
          ) : artifact.ready ? (
            <span className="text-[10px] font-mono rounded-full border px-1.5 py-0.5 bg-accent/20 text-accent border-accent/40">
              就绪
            </span>
          ) : null}
        </span>
      </div>
      <p className="text-[11px] text-slate-500 ml-7 truncate">{artifact.subtitle}</p>
      {artifact.modal ? (
        <p className="text-[10px] text-slate-600 ml-7 mt-1">点击打开工作区 Modal</p>
      ) : (
        <p className="text-[10px] text-slate-600 ml-7 mt-1">见研究对话与左栏资料</p>
      )}
    </button>
  );
}

export default function ArtifactDrawer() {
  const {
    artifactDrawerOpen,
    activeArtifactId,
    toggleArtifactDrawer,
    openArtifact,
    leaderboard,
    formulationBusy,
    doePlan,
    busy,
    optimizationHistory,
    deepReport,
    deepResearchBusy,
    loopReport,
  } = useStore(
    useShallow((s) => ({
      artifactDrawerOpen: s.artifactDrawerOpen,
      activeArtifactId: s.activeArtifactId,
      toggleArtifactDrawer: s.toggleArtifactDrawer,
      openArtifact: s.openArtifact,
      leaderboard: s.leaderboard,
      formulationBusy: s.formulationBusy,
      doePlan: s.doePlan,
      busy: s.busy,
      optimizationHistory: s.optimizationHistory,
      deepReport: s.deepReport,
      deepResearchBusy: s.deepResearchBusy,
      loopReport: s.loopReport,
    })),
  );

  const artifacts = useMemo(
    () =>
      selectProjectArtifacts({
        leaderboard,
        formulationBusy,
        doePlan,
        busy,
        optimizationHistory,
        deepReport,
        deepResearchBusy,
        loopReport,
      }),
    [
      leaderboard,
      formulationBusy,
      doePlan,
      busy,
      optimizationHistory,
      deepReport,
      deepResearchBusy,
      loopReport,
    ],
  );

  if (!artifactDrawerOpen) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-30 bg-ink/60 backdrop-blur-sm"
        onClick={toggleArtifactDrawer}
        data-testid="artifact-drawer-overlay"
      />
      <div
        className="fixed right-0 top-0 h-full w-96 z-40 bg-panel border-l border-edge flex flex-col shadow-2xl"
        data-testid="artifact-drawer"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-edge">
          <h2 className="text-sm font-semibold text-slate-200">产物工作区</h2>
          <button
            onClick={toggleArtifactDrawer}
            className="text-slate-400 hover:text-slate-200 w-6 h-6 flex items-center justify-center rounded hover:bg-edge"
            aria-label="关闭"
            data-testid="artifact-drawer-close"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {artifacts.length === 0 ? (
            <p className="text-slate-500 text-sm text-center mt-8 px-4">
              暂无产物。
              <br />
              <span className="text-xs">
                运行推荐配方、DOE、寻优或深度研究后，结果会出现在此。
              </span>
            </p>
          ) : (
            artifacts.map((a) => (
              <ArtifactCard
                key={a.id}
                artifact={a}
                active={a.id === activeArtifactId}
                onOpen={() => openArtifact(a.id)}
              />
            ))
          )}
        </div>

        <div className="px-4 py-2 border-t border-edge text-[10px] text-slate-600">
          {artifacts.length} 个活产物 · 派生自当前会话状态（无沙箱落盘）
        </div>
      </div>
    </>
  );
}
