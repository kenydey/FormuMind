import { useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../store";

function checklistStatus(
  itemId: string,
  thinking: { id: string; status?: string }[],
  busy: boolean,
): "pending" | "running" | "done" {
  const hit = thinking.find((t) => t.id === itemId);
  if (hit?.status === "done") return "done";
  if (hit?.status === "running") return "running";
  if (hit?.status === "error") return "pending";
  if (!busy) return "pending";
  return "pending";
}

/** Compact active-playbook checklist (no skills marketplace). Hidden when idle. */
export default function ActivePlaybookStrip() {
  const {
    activeSkillId,
    activeSkill,
    clearFormulationSkill,
    taskThinking,
    formulationBusy,
    busy,
    deepResearchBusy,
    preferMaterialsCatalog,
  } = useStore(
    useShallow((s) => ({
      activeSkillId: s.activeSkillId,
      activeSkill: s.activeSkill,
      clearFormulationSkill: s.clearFormulationSkill,
      taskThinking: s.taskThinking,
      formulationBusy: s.formulationBusy,
      busy: s.busy,
      deepResearchBusy: s.deepResearchBusy,
      preferMaterialsCatalog: s.preferMaterialsCatalog,
    })),
  );

  const runBusy =
    formulationBusy ||
    deepResearchBusy ||
    busy === "optimizing" ||
    busy === "doe" ||
    busy === "looping";

  const active = useMemo(() => {
    if (!activeSkillId || !activeSkill) return null;
    return activeSkill.id === activeSkillId ? activeSkill : null;
  }, [activeSkill, activeSkillId]);

  if (!active) return null;

  return (
    <section
      className="rounded-lg border border-accent/25 bg-accent/5 p-2.5 space-y-2"
      data-testid="active-playbook-strip"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold text-slate-200 min-w-0 truncate">
          <span className="mr-1" aria-hidden>
            {active.icon}
          </span>
          {active.title}
        </div>
        <button
          type="button"
          onClick={clearFormulationSkill}
          className="text-[10px] text-slate-500 hover:text-slate-300 shrink-0"
          data-testid="clear-skill"
        >
          清除
        </button>
      </div>
      <p className="text-[10px] text-slate-500">{active.summary}</p>
      {active.action === "deep_research" && (
        <p className="text-[10px] text-amber-300/90">
          请在中栏点击「深度研究」启动（已按当前产品域写入检索提示）。
        </p>
      )}
      {active.action === "recommend" && preferMaterialsCatalog && (
        <p className="text-[10px] text-emerald-400/80">已开启「优先材料库」软偏好</p>
      )}
      {active.checklist.length > 0 && (
        <ol className="space-y-0.5">
          {active.checklist.map((c) => {
            const st = checklistStatus(c.id, taskThinking, runBusy);
            return (
              <li
                key={c.id}
                className="flex items-center gap-1.5 text-[10px] text-slate-400"
                data-testid={`skill-check-${c.id}`}
                data-status={st}
              >
                <span className="font-mono w-3 text-center">
                  {st === "done" ? "✓" : st === "running" ? "›" : "·"}
                </span>
                <span className={st === "running" ? "text-accent animate-pulse" : ""}>
                  {c.title}
                </span>
              </li>
            );
          })}
        </ol>
      )}
      <p className="text-[9px] text-slate-600">
        行动包在设置管理，中栏「+」启动；此处仅显示当前活跃清单。
      </p>
    </section>
  );
}
