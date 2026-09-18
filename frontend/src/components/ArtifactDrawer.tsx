import { useCallback, useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  selectProjectArtifacts,
  type ArtifactKind,
  type ProjectArtifact,
} from "../artifacts/projectArtifacts";
import { api, type ProjectExportFile } from "../api";
import { useStore } from "../store";
import { saveTextToProjectShelf, shelfFilename } from "../utils/export";

const KIND_ICON: Record<ArtifactKind, string> = {
  leaderboard: "⭐",
  doe_plan: "🔬",
  optimization: "📈",
  deep_report: "📑",
  loop_report: "🔄",
  wiki_report: "📗",
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
      {artifact.modal === "knowledge" ? (
        <p className="text-[10px] text-slate-600 ml-7 mt-1">点击打开知识库 · 文档生成</p>
      ) : artifact.modal ? (
        <p className="text-[10px] text-slate-600 ml-7 mt-1">点击打开工作区 Modal</p>
      ) : (
        <p className="text-[10px] text-slate-600 ml-7 mt-1">见研究对话与左栏资料</p>
      )}
    </button>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function ExportShelfPanel({ projectId }: { projectId: string }) {
  const [files, setFiles] = useState<ProjectExportFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const leaderboard = useStore((s) => s.leaderboard);
  const deepReport = useStore((s) => s.deepReport);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setFiles(await api.listProjectExports(projectId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
      setFiles([]);
    } finally {
      setBusy(false);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function saveLeaderboard() {
    if (!leaderboard.length) return;
    setBusy(true);
    try {
      await saveTextToProjectShelf(
        projectId,
        shelfFilename(`leaderboard_${leaderboard.length}`, "json"),
        JSON.stringify(leaderboard, null, 2),
      );
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
      setBusy(false);
    }
  }

  async function saveDeepReport() {
    const md = deepReport?.report_markdown?.trim() || "";
    if (!md) return;
    setBusy(true);
    try {
      await saveTextToProjectShelf(projectId, shelfFilename("deep_report", "md"), md);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
      setBusy(false);
    }
  }

  async function removeFile(name: string) {
    setBusy(true);
    try {
      await api.deleteProjectExport(projectId, name);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2" data-testid="export-shelf-panel">
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          disabled={busy || leaderboard.length === 0}
          onClick={() => void saveLeaderboard()}
          className="text-[10px] border border-accent/40 text-accent rounded px-2 py-1 hover:bg-accent/10 disabled:opacity-40"
          data-testid="shelf-save-leaderboard"
        >
          保存推荐榜 JSON
        </button>
        <button
          type="button"
          disabled={busy || !deepReport}
          onClick={() => void saveDeepReport()}
          className="text-[10px] border border-edge text-slate-400 rounded px-2 py-1 hover:text-accent hover:border-accent/40 disabled:opacity-40"
          data-testid="shelf-save-deep-report"
        >
          保存深度报告
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void refresh()}
          className="text-[10px] border border-edge text-slate-500 rounded px-2 py-1 hover:text-slate-300 ml-auto"
        >
          刷新
        </button>
      </div>
      {error && (
        <p className="text-[11px] text-rose-300 border border-rose-500/30 rounded px-2 py-1">{error}</p>
      )}
      {busy && files.length === 0 ? (
        <p className="text-slate-500 text-xs text-center py-6">加载中…</p>
      ) : files.length === 0 ? (
        <p className="text-slate-500 text-sm text-center mt-6 px-3">
          货架为空。
          <br />
          <span className="text-xs">从推荐榜导出菜单「保存到货架」，或点上方快捷按钮。</span>
        </p>
      ) : (
        files.map((f) => (
          <div
            key={f.name}
            className="border border-edge/50 rounded-lg px-3 py-2 bg-ink/60 flex items-start gap-2"
            data-testid={`shelf-file-${f.name}`}
          >
            <div className="min-w-0 flex-1">
              <div className="text-xs text-slate-200 font-mono truncate">{f.name}</div>
              <div className="text-[10px] text-slate-500 mt-0.5">
                {formatBytes(f.size)} · {new Date(f.updated_at).toLocaleString("zh-CN")}
              </div>
            </div>
            <a
              href={api.downloadProjectExportUrl(projectId, f.name)}
              download={f.name}
              className="text-[10px] text-accent border border-accent/30 rounded px-1.5 py-0.5 hover:bg-accent/10 shrink-0"
            >
              下载
            </a>
            <button
              type="button"
              onClick={() => void removeFile(f.name)}
              className="text-[10px] text-rose-400 border border-rose-500/30 rounded px-1.5 py-0.5 hover:bg-rose-500/10 shrink-0"
            >
              删除
            </button>
          </div>
        ))
      )}
    </div>
  );
}

export default function ArtifactDrawer() {
  const [tab, setTab] = useState<"live" | "shelf">("live");
  const {
    artifactDrawerOpen,
    activeArtifactId,
    toggleArtifactDrawer,
    openArtifact,
    activeProjectId,
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
      activeProjectId: s.activeProjectId,
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
        activeProjectId,
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
      activeProjectId,
    ],
  );

  useEffect(() => {
    if (artifactDrawerOpen) setTab("live");
  }, [artifactDrawerOpen]);

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

        <div className="flex gap-1 px-3 pt-2 border-b border-edge/60">
          <button
            type="button"
            onClick={() => setTab("live")}
            data-testid="artifact-tab-live"
            className={`text-[11px] px-2.5 py-1.5 rounded-t border-b-2 ${
              tab === "live"
                ? "border-accent text-accent"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            活产物 {artifacts.length > 0 ? `(${artifacts.length})` : ""}
          </button>
          <button
            type="button"
            onClick={() => setTab("shelf")}
            data-testid="artifact-tab-shelf"
            className={`text-[11px] px-2.5 py-1.5 rounded-t border-b-2 ${
              tab === "shelf"
                ? "border-accent text-accent"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            导出货架
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {tab === "live" ? (
            artifacts.length === 0 ? (
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
            )
          ) : !activeProjectId ? (
            <p className="text-slate-500 text-sm text-center mt-8 px-4">
              请先打开或新建项目，再使用导出货架。
            </p>
          ) : (
            <ExportShelfPanel projectId={activeProjectId} />
          )}
        </div>

        <div className="px-4 py-2 border-t border-edge text-[10px] text-slate-600">
          {tab === "live"
            ? `${artifacts.length} 个活产物 · 派生自当前会话状态`
            : "导出货架 · 项目级落盘（无 Docker 沙箱）"}
        </div>
      </div>
    </>
  );
}
