/**
 * W3-6: 项目设置 —— agent_context 多行输入框。
 * 后端字段 + prompt 注入已在 Wave 2 落地；此处提供多行编辑 UI，
 * 保存走现有项目设置 API（store.setAgentContext → autosave → PUT /api/projects/{id}）。
 */
import { useEffect, useState } from "react";
import { useStore } from "../store";

export default function ProjectSettingsPanel() {
  const activeProjectId = useStore((s) => s.activeProjectId);
  const projects = useStore((s) => s.projects);
  const agentContext = useStore((s) => s.agentContext);
  const setAgentContext = useStore((s) => s.setAgentContext);
  const projectSaveBusy = useStore((s) => s.projectSaveBusy);

  const [draft, setDraft] = useState(agentContext);
  const [savedTick, setSavedTick] = useState(0);

  // 切换项目 / 外部加载后同步草稿
  useEffect(() => {
    setDraft(agentContext);
  }, [agentContext, activeProjectId]);

  const dirty = draft !== agentContext;
  const activeProject = projects.find((p) => p.id === activeProjectId);

  function onSave() {
    setAgentContext(draft);
    setSavedTick((t) => t + 1);
  }

  if (!activeProjectId) {
    return (
      <p className="text-xs text-slate-500 py-4 text-center" data-testid="project-settings-panel">
        请先打开或新建项目，再编辑项目设置。
      </p>
    );
  }

  return (
    <div className="space-y-3" data-testid="project-settings-panel">
      <div className="text-xs text-slate-500">
        当前项目：
        <span className="text-slate-300">{activeProject?.title || activeProjectId}</span>
      </div>

      <label
        htmlFor="agent-context-textarea"
        className="block text-sm text-slate-200"
        data-testid="agent-context-label"
      >
        Agent 上下文 <span className="text-slate-500 font-mono text-[10px]">agent_context</span>
      </label>
      <textarea
        id="agent-context-textarea"
        data-testid="agent-context-textarea"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={8}
        placeholder={"例如：\n- 本项目聚焦水性环氧防腐涂料\n- 优先引用近 5 年文献\n- 回答用中文，数值保留 2 位小数"}
        className="w-full bg-ink border border-edge rounded px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-accent/60 resize-y min-h-[120px]"
      />
      <p className="text-[11px] text-slate-500 leading-relaxed">
        保存后会自动注入到本项目的 Agent prompt 中（证据综合 / 问答时生效）。
        留空则跳过注入，与旧数据向后兼容。
      </p>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onSave}
          disabled={!dirty || projectSaveBusy}
          className="text-xs border border-accent text-accent rounded px-3 py-1.5 hover:bg-accent/10 disabled:opacity-40"
          data-testid="agent-context-save"
        >
          {projectSaveBusy ? "保存中…" : "保存"}
        </button>
        {dirty ? (
          <span className="text-[11px] text-amber-400" data-testid="agent-context-dirty">
            有未保存的修改
          </span>
        ) : (
          savedTick > 0 && (
            <span className="text-[11px] text-emerald-400" data-testid="agent-context-saved">
              ✓ 已保存
            </span>
          )
        )}
      </div>
    </div>
  );
}
