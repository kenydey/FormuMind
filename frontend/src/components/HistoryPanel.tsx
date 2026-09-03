import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore, type ProjectSummary } from "../store";
import { api } from "../api";
import { SOURCE_LIMIT } from "../projectWorkspace";

function fmt(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function ProjectCard({
  project,
  active,
  onOpen,
  onDelete,
}: {
  project: ProjectSummary;
  active: boolean;
  onOpen: () => void;
  onDelete: (e: React.MouseEvent) => void;
}) {
  return (
    <div
      className={`w-full text-left border rounded-lg p-3 transition-colors ${
        active
          ? "border-accent/50 bg-accent/10"
          : "border-edge/50 bg-ink/60 hover:border-accent/40 hover:bg-accent/5"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <button type="button" onClick={onOpen} className="flex-1 min-w-0 text-left">
          <div className="flex items-center gap-1.5 mb-0.5 flex-wrap">
            <span className="text-[10px] uppercase tracking-widest text-accent2 font-semibold truncate">
              {project.title || "未命名项目"}
            </span>
            <span className="text-[10px] text-slate-600">·</span>
            <span className="text-[10px] text-slate-500 font-mono">{fmt(project.updated_at)}</span>
          </div>
          <p className="text-xs text-slate-300 truncate">{project.headline}</p>
          <div className="flex flex-wrap gap-1 mt-1.5">
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-edge/80 text-slate-400">
              资料 {project.source_count}
            </span>
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-edge/80 text-slate-400">
              对话 {project.chat_count}
            </span>
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-edge/80 text-slate-400">
              配方 {project.leaderboard_count}
            </span>
            {project.has_doe && (
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent2/20 text-accent2">DOE</span>
            )}
            {project.has_optimize && (
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400">寻优</span>
            )}
            {project.has_loop && (
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-violet-500/20 text-violet-300">闭环</span>
            )}
          </div>
        </button>
        <button
          type="button"
          onClick={onDelete}
          className="shrink-0 text-[10px] text-slate-600 hover:text-rose-400 px-1"
          title="删除项目"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

export default function HistoryPanel() {
  const {
    historyOpen,
    projects,
    activeProjectId,
    sources,
    projectSaveBusy,
    toggleHistory,
    loadProject,
    createProject,
    deleteProject,
  } = useStore(
    useShallow((s) => ({
      historyOpen: s.historyOpen,
      projects: s.projects,
      activeProjectId: s.activeProjectId,
      sources: s.sources,
      projectSaveBusy: s.projectSaveBusy,
      toggleHistory: s.toggleHistory,
      loadProject: s.loadProject,
      createProject: s.createProject,
      deleteProject: s.deleteProject,
    }))
  );

  const [pendingDelete, setPendingDelete] = useState<
    { project: ProjectSummary; documentCount: number; busy: boolean } | null
  >(null);

  async function requestDelete(project: ProjectSummary) {
    try {
      const stats = await api.getProjectDbStats(project.id);
      setPendingDelete({ project, documentCount: stats.document_count, busy: false });
    } catch {
      void deleteProject(project.id);
    }
  }

  async function confirmDelete(knowledge: "delete" | "global") {
    if (!pendingDelete) return;
    setPendingDelete((p) => (p ? { ...p, busy: true } : p));
    await deleteProject(pendingDelete.project.id, knowledge);
    setPendingDelete(null);
  }

  if (!historyOpen) return null;

  return (
    <>
      <div className="fixed inset-0 z-30 bg-ink/60 backdrop-blur-sm" onClick={toggleHistory} />
      <div className="fixed right-0 top-0 h-full w-96 z-40 bg-panel border-l border-edge flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-4 py-3 border-b border-edge">
          <h2 className="text-sm font-semibold text-slate-200">项目历史</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void createProject()}
              className="text-[10px] text-accent border border-accent/40 rounded px-2 py-0.5 hover:bg-accent/10"
            >
              + 新建
            </button>
            <button
              onClick={toggleHistory}
              className="text-slate-400 hover:text-slate-200 w-6 h-6 flex items-center justify-center rounded hover:bg-edge"
              aria-label="关闭"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {projects.length === 0 ? (
            <p className="text-slate-500 text-sm text-center mt-8">
              暂无项目。<br />
              <span className="text-xs">点击「新建」开始研究。</span>
            </p>
          ) : (
            projects.map((p) => (
              <ProjectCard
                key={p.id}
                project={p}
                active={p.id === activeProjectId}
                onOpen={() => void loadProject(p.id)}
                onDelete={(e) => {
                  e.stopPropagation();
                  void requestDelete(p);
                }}
              />
            ))
          )}
        </div>

        <div className="px-4 py-2 border-t border-edge text-[10px] text-slate-600 space-y-1">
          <div>
            {projects.length} 个项目 · SQLite 本地库
            {projectSaveBusy && <span className="ml-2 text-accent">保存中…</span>}
          </div>
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-edge rounded overflow-hidden">
              <div
                className="h-full bg-accent transition-all"
                style={{ width: `${Math.min(100, (sources.length / SOURCE_LIMIT) * 100)}%` }}
              />
            </div>
            <span className="font-mono shrink-0">
              {sources.length} / {SOURCE_LIMIT}
            </span>
          </div>
        </div>
      </div>

      {pendingDelete && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => !pendingDelete.busy && setPendingDelete(null)}
        >
          <div
            className="bg-panel border border-edge rounded-lg shadow-xl w-[26rem] max-w-[92vw] p-4 text-sm"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="font-semibold text-slate-200 mb-2">
              删除项目「{pendingDelete.project.title || pendingDelete.project.headline}」
            </h3>
            {pendingDelete.documentCount > 0 ? (
              <>
                <p className="text-slate-400 text-xs mb-3">
                  该项目包含{" "}
                  <span className="text-accent">{pendingDelete.documentCount} 篇知识文档</span>
                  。删除时如何处理？
                </p>
                <div className="space-y-2">
                  <button
                    className="w-full text-left border border-accent/40 bg-accent/10 rounded px-3 py-2 hover:bg-accent/20 disabled:opacity-50"
                    disabled={pendingDelete.busy}
                    onClick={() => void confirmDelete("global")}
                  >
                    <span className="text-accent font-medium">转入全局知识库</span>
                    <span className="block text-xs text-slate-400">
                      文档归属清空，其他项目问答/推荐可调用
                    </span>
                  </button>
                  <button
                    className="w-full text-left border border-rose-500/40 bg-rose-500/10 rounded px-3 py-2 hover:bg-rose-500/20 disabled:opacity-50"
                    disabled={pendingDelete.busy}
                    onClick={() => void confirmDelete("delete")}
                  >
                    <span className="text-rose-400 font-medium">一并删除</span>
                    <span className="block text-xs text-slate-400">
                      知识库 + 业务数据全部删除，不再保留
                    </span>
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="text-slate-400 text-xs mb-3">
                  该项目无知识文档，删除将清除其业务数据。
                </p>
                <button
                  className="w-full bg-rose-500/90 text-ink rounded px-3 py-2 font-semibold disabled:opacity-50"
                  disabled={pendingDelete.busy}
                  onClick={() => void confirmDelete("delete")}
                >
                  {pendingDelete.busy ? "删除中…" : "确认删除"}
                </button>
              </>
            )}
            <button
              className="w-full mt-2 border border-edge rounded px-3 py-1.5 text-slate-400 hover:text-slate-200 disabled:opacity-50"
              disabled={pendingDelete.busy}
              onClick={() => setPendingDelete(null)}
            >
              取消
            </button>
          </div>
        </div>
      )}
    </>
  );
}
