/**
 * Wave 0: 项目 Agent 上下文编辑 —— 从设置页「项目」tab 搬到项目面板旁。
 * 只在有活跃项目时渲染；折叠式，不占常驻空间。
 */
import { useEffect, useState } from "react";
import { useStore } from "../store";

export default function ProjectAgentContext() {
  const activeProjectId = useStore((s) => s.activeProjectId);
  const projects = useStore((s) => s.projects);
  const agentContext = useStore((s) => s.agentContext);
  const setAgentContext = useStore((s) => s.setAgentContext);
  const projectSaveBusy = useStore((s) => s.projectSaveBusy);

  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(agentContext);
  const [savedTick, setSavedTick] = useState(0);

  useEffect(() => {
    setDraft(agentContext);
  }, [agentContext, activeProjectId]);

  if (!activeProjectId) return null;
  const activeProject = projects.find((p) => p.id === activeProjectId);
  const dirty = draft !== agentContext;

  function onSave() {
    setAgentContext(draft);
    setSavedTick((t) => t + 1);
  }

  return (
    <div className="border-t border-edge" data-testid="project-agent-context">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-testid="project-agent-context-toggle"
        className="w-full flex items-center justify-between px-3 py-2 text-xs text-slate-400 hover:text-slate-200"
      >
        <span>
          Agent 上下文
          <span className="ml-2 text-[10px] text-slate-600 truncate inline-block max-w-[180px] align-middle">
            {agentContext ? agentContext.split("\n")[0] : "未设置"}
          </span>
        </span>
        <span className="text-slate-600">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-2">
          <div className="text-[10px] text-slate-600">
            当前项目：<span className="text-slate-400">{activeProject?.title || activeProjectId}</span>
          </div>
          <label htmlFor="agent-context-textarea" className="sr-only">
            Agent 上下文
          </label>
          <textarea
            id="agent-context-textarea"
            data-testid="agent-context-textarea"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={6}
            placeholder={"例如：\n- 本项目聚焦水性环氧防腐涂料\n- 优先引用近 5 年文献\n- 回答用中文，数值保留 2 位小数"}
            className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-accent/60 resize-y min-h-[90px]"
          />
          <p className="text-[10px] text-slate-600 leading-relaxed">
            保存后自动注入本项目 Agent prompt（证据综合 / 问答时生效）；留空跳过注入。
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onSave}
              disabled={!dirty || projectSaveBusy}
              className="text-xs border border-accent text-accent rounded px-3 py-1 hover:bg-accent/10 disabled:opacity-40"
              data-testid="agent-context-save"
            >
              {projectSaveBusy ? "保存中…" : "保存"}
            </button>
            {dirty ? (
              <span className="text-[10px] text-amber-400" data-testid="agent-context-dirty">
                有未保存的修改
              </span>
            ) : (
              savedTick > 0 && (
                <span className="text-[10px] text-emerald-400" data-testid="agent-context-saved">
                  ✓ 已保存
                </span>
              )
            )}
          </div>
        </div>
      )}
    </div>
  );
}
