import { useCallback, useEffect, useState } from "react";
import { api, formatApiError, type UnifiedSkill } from "../api";

export default function SkillsSettingsPanel({ reloadKey = 0 }: { reloadKey?: number }) {
  const [skills, setSkills] = useState<UnifiedSkill[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(null);
    return api
      .listSkills()
      .then((res) => setSkills(res.skills))
      .catch((e) => setError(formatApiError(e)));
  }, []);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  async function toggle(skill: UnifiedSkill, enabled: boolean) {
    setBusy(true);
    try {
      const disabled = skills
        .filter((s) => (s.id === skill.id ? !enabled : !s.enabled))
        .filter((s) => s.activation_policy !== "always-on")
        .map((s) => s.id);
      const pinned = skills.filter((s) => s.pinned).map((s) => s.id);
      const res = await api.patchSkillsPrefs({ disabled_ids: disabled, pinned_ids: pinned });
      setSkills(res.skills);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }

  const playbooks = skills.filter((s) => s.kind === "playbook");
  const chatSkills = skills.filter((s) => s.kind === "chat_skill");

  return (
    <div className="space-y-4" data-testid="skills-settings-panel">
      <p className="text-xs text-slate-500 leading-relaxed">
        统一管理<strong className="text-slate-300">配方行动包</strong>（打开 DOE/寻优等）与
        <strong className="text-slate-300">对话技能</strong>（SKILL.md，注入问答提示）。关闭后中栏「+」不可选用。
      </p>
      {error && (
        <p className="text-xs text-rose-300 border border-rose-500/30 rounded px-2 py-1">{error}</p>
      )}
      <Section title="配方行动包 · Playbooks" rows={playbooks} busy={busy} onToggle={toggle} />
      <Section title="对话技能 · Chat Skills" rows={chatSkills} busy={busy} onToggle={toggle} />
    </div>
  );
}

function Section({
  title,
  rows,
  busy,
  onToggle,
}: {
  title: string;
  rows: UnifiedSkill[];
  busy: boolean;
  onToggle: (s: UnifiedSkill, enabled: boolean) => void;
}) {
  return (
    <div>
      <h3 className="text-xs uppercase tracking-widest text-accent2 mb-2">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-[11px] text-slate-600">暂无</p>
      ) : (
        <ul className="space-y-2">
          {rows.map((s) => (
            <li
              key={s.id}
              className="flex items-start gap-3 rounded border border-edge/60 bg-ink/40 px-2.5 py-2"
              data-testid={`skill-row-${s.id}`}
            >
              <span className="text-base shrink-0">{s.icon || "✦"}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-slate-200">{s.title}</div>
                <div className="text-[11px] text-slate-500 mt-0.5 line-clamp-2">{s.summary}</div>
                <div className="text-[10px] text-slate-600 mt-1">
                  {s.kind} · {s.activation_policy}
                  {s.origin ? ` · ${s.origin}` : ""}
                </div>
              </div>
              <label className="flex items-center gap-1.5 text-[11px] text-slate-400 shrink-0">
                <input
                  type="checkbox"
                  checked={s.enabled}
                  disabled={busy || s.activation_policy === "always-on"}
                  onChange={(e) => onToggle(s, e.target.checked)}
                />
                启用
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
