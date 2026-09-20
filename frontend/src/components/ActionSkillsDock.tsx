import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, type FormulationSkill } from "../api";
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
  // If busy and earlier thinking steps done but this id not present yet → pending
  return "pending";
}

export default function ActionSkillsDock() {
  const [skills, setSkills] = useState<FormulationSkill[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const {
    activeSkillId,
    activeSkill,
    applyFormulationSkill,
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
      applyFormulationSkill: s.applyFormulationSkill,
      clearFormulationSkill: s.clearFormulationSkill,
      taskThinking: s.taskThinking,
      formulationBusy: s.formulationBusy,
      busy: s.busy,
      deepResearchBusy: s.deepResearchBusy,
      preferMaterialsCatalog: s.preferMaterialsCatalog,
    })),
  );

  useEffect(() => {
    let cancelled = false;
    void api
      .listFormulationSkills()
      .then((rows) => {
        if (!cancelled) setSkills(rows);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : "技能加载失败");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const runBusy =
    formulationBusy ||
    deepResearchBusy ||
    busy === "optimizing" ||
    busy === "doe" ||
    busy === "looping";

  const active = useMemo(
    () => activeSkill || skills.find((s) => s.id === activeSkillId) || null,
    [activeSkill, activeSkillId, skills],
  );

  return (
    <section
      className="rounded-lg border border-edge/60 bg-ink/40 p-2.5 space-y-2"
      data-testid="action-skills-dock"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[10px] uppercase tracking-widest text-accent2">技能坞 · Skills</h3>
        {active && (
          <button
            type="button"
            onClick={clearFormulationSkill}
            className="text-[10px] text-slate-500 hover:text-slate-300"
            data-testid="clear-skill"
          >
            清除
          </button>
        )}
      </div>

      {loadError && (
        <p className="text-[10px] text-rose-300 border border-rose-500/30 rounded px-2 py-1">{loadError}</p>
      )}

      <div className="flex flex-wrap gap-1.5" data-testid="skills-rail">
        {skills.map((s) => {
          const on = s.id === activeSkillId;
          return (
            <button
              key={s.id}
              type="button"
              title={s.summary}
              data-testid={`skill-chip-${s.id}`}
              onClick={() => applyFormulationSkill(s)}
              className={`text-[10px] rounded-full border px-2 py-1 transition-colors ${
                on
                  ? "border-accent bg-accent/15 text-accent"
                  : "border-edge text-slate-400 hover:border-accent/40 hover:text-accent"
              }`}
            >
              <span className="mr-1" aria-hidden>
                {s.icon}
              </span>
              {s.title}
            </button>
          );
        })}
      </div>

      {active ? (
        <div className="rounded border border-accent/20 bg-accent/5 px-2.5 py-2 space-y-2" data-testid="active-skill-panel">
          <div>
            <div className="text-xs font-semibold text-slate-200">
              {active.icon} {active.title}
            </div>
            <p className="text-[10px] text-slate-500 mt-0.5">{active.summary}</p>
            {active.action === "deep_research" && (
              <p className="text-[10px] text-amber-300/90 mt-1">
                请在中栏点击「深度研究」启动（技能已写入检索提示词）。
              </p>
            )}
            {active.action === "recommend" && preferMaterialsCatalog && (
              <p className="text-[10px] text-emerald-400/80 mt-1">已开启「优先材料库」软偏好</p>
            )}
          </div>

          <div>
            <div className="text-[9px] uppercase tracking-widest text-slate-600 mb-1">工具</div>
            <div className="flex flex-wrap gap-1">
              {active.tools.map((t) => (
                <span
                  key={t}
                  className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-edge/60 text-slate-400"
                >
                  {t}
                </span>
              ))}
            </div>
          </div>

          {active.checklist.length > 0 && (
            <div>
              <div className="text-[9px] uppercase tracking-widest text-slate-600 mb-1">清单</div>
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
            </div>
          )}
        </div>
      ) : (
        <p className="text-[10px] text-slate-600">
          选择一条涂料 playbook，将预设并打开对应操作（不替代右侧 Actions 瓷砖）。
        </p>
      )}
    </section>
  );
}
