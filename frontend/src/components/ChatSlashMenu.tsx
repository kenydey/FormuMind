import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, type UnifiedSkill } from "../api";
import { useStore } from "../store";

/** Detect trailing `/query` token for Claude-style skill slash picker. */
export function slashQuery(draft: string): { active: boolean; query: string; prefix: string } {
  const m = draft.match(/(^|\s)\/([a-zA-Z0-9_\-]*)$/);
  if (!m) return { active: false, query: "", prefix: draft };
  const token = m[2] || "";
  const prefix = draft.slice(0, draft.length - token.length - 1);
  return { active: true, query: token.toLowerCase(), prefix };
}

export default function ChatSlashMenu({
  draft,
  onApply,
}: {
  draft: string;
  onApply: (nextDraft: string) => void;
}) {
  const [skills, setSkills] = useState<UnifiedSkill[]>([]);
  const { toggleSelectedChatSkill, applyFormulationSkill, selectedChatSkills } = useStore(
    useShallow((s) => ({
      toggleSelectedChatSkill: s.toggleSelectedChatSkill,
      applyFormulationSkill: s.applyFormulationSkill,
      selectedChatSkills: s.selectedChatSkills,
    })),
  );

  const { active, query, prefix } = useMemo(() => slashQuery(draft), [draft]);

  useEffect(() => {
    if (!active) return;
    void api.listSkills().then((r) => setSkills(r.skills.filter((x) => x.enabled)));
  }, [active]);

  if (!active) return null;

  const filtered = skills
    .filter((s) => !query || s.id.toLowerCase().includes(query) || s.title.toLowerCase().includes(query))
    .slice(0, 12);

  if (filtered.length === 0) {
    return (
      <div
        className="absolute bottom-full left-10 mb-1 w-64 rounded-lg border border-edge bg-panel shadow-xl z-30 p-2 text-[11px] text-slate-500"
        data-testid="chat-slash-menu"
      >
        无匹配技能 — 在设置中安装或启用
      </div>
    );
  }

  return (
    <div
      className="absolute bottom-full left-10 mb-1 w-72 max-h-56 overflow-auto rounded-lg border border-edge bg-panel shadow-xl z-30 p-1.5 space-y-0.5"
      data-testid="chat-slash-menu"
    >
      <div className="text-[9px] uppercase tracking-widest text-slate-600 px-1.5 py-1">
        / 技能 · Skills
      </div>
      {filtered.map((s) => {
        const on = selectedChatSkills.includes(s.id);
        return (
          <button
            key={s.id}
            type="button"
            data-testid={`slash-skill-${s.id}`}
            className={`w-full text-left px-2 py-1.5 rounded text-[11px] hover:bg-accent/10 ${
              on ? "text-accent" : "text-slate-300"
            }`}
            onClick={() => {
              if (s.kind === "playbook") {
                applyFormulationSkill({
                  id: s.id,
                  title: s.title,
                  summary: s.summary,
                  when_to_use: s.when_to_use || "",
                  action: s.action,
                  modal: s.modal,
                  icon: s.icon,
                  tools: s.tools,
                  checklist: s.checklist || [],
                  presets: s.presets || {},
                });
              } else {
                toggleSelectedChatSkill(s.id);
              }
              onApply(prefix.trimEnd() ? `${prefix.trimEnd()} ` : "");
            }}
          >
            <span className="mr-1">{s.icon}</span>
            <span className="font-mono text-accent/90">/{s.id}</span>
            <span className="text-slate-500 ml-1">{s.title}</span>
          </button>
        );
      })}
    </div>
  );
}
