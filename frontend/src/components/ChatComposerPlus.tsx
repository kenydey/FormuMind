import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, type BuiltinConnector, type UnifiedSkill } from "../api";
import { useStore } from "../store";

export default function ChatComposerPlus() {
  const [open, setOpen] = useState(false);
  const [skills, setSkills] = useState<UnifiedSkill[]>([]);
  const [connectors, setConnectors] = useState<BuiltinConnector[]>([]);
  const rootRef = useRef<HTMLDivElement>(null);

  const {
    chatComposerPlusEnabled,
    chatMode,
    selectedChatSkills,
    selectedConnectors,
    setChatMode,
    toggleSelectedChatSkill,
    toggleSelectedConnector,
    applyFormulationSkill,
    sources,
    selectedSources,
    appendChatDraftRef,
  } = useStore(
    useShallow((s) => ({
      chatComposerPlusEnabled: s.chatComposerPlusEnabled,
      chatMode: s.chatMode,
      selectedChatSkills: s.selectedChatSkills,
      selectedConnectors: s.selectedConnectors,
      setChatMode: s.setChatMode,
      toggleSelectedChatSkill: s.toggleSelectedChatSkill,
      toggleSelectedConnector: s.toggleSelectedConnector,
      applyFormulationSkill: s.applyFormulationSkill,
      sources: s.sources,
      selectedSources: s.selectedSources,
      appendChatDraftRef: s.appendChatDraftRef,
    })),
  );

  useEffect(() => {
    if (!open) return;
    void api.listSkills().then((r) => setSkills(r.skills.filter((x) => x.enabled)));
    void api.listConnectors().then((r) => setConnectors(r.builtin.filter((c) => c.enabled)));
  }, [open]);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (!chatComposerPlusEnabled) return null;

  const chatSkills = skills.filter((s) => s.kind === "chat_skill");
  const playbooks = skills.filter((s) => s.kind === "playbook");
  const selected = sources.filter((e) => selectedSources.includes(e.identifier || e.title));

  return (
    <div className="relative" ref={rootRef} data-testid="chat-composer-plus">
      <button
        type="button"
        title="附件 · Skills · Evidence · Connectors"
        onClick={() => setOpen((v) => !v)}
        className="bg-ink border border-edge hover:border-accent/50 rounded px-2.5 py-1.5 text-sm shrink-0"
        data-testid="chat-plus-btn"
      >
        +
      </button>
      {open && (
        <div className="absolute bottom-full left-0 mb-1 w-72 max-h-80 overflow-auto rounded-lg border border-edge bg-panel shadow-xl z-30 p-2 space-y-2 text-[11px]">
          <div>
            <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">模式</div>
            <div className="flex gap-1">
              {(["chat", "evidence"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setChatMode(m)}
                  className={`px-2 py-1 rounded border ${
                    chatMode === m
                      ? "border-accent text-accent bg-accent/10"
                      : "border-edge text-slate-400"
                  }`}
                >
                  {m === "chat" ? "普通问答" : "文献综合"}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">
              @ 引用已选资料
            </div>
            {selected.length === 0 ? (
              <p className="text-slate-600">先勾选左栏资料</p>
            ) : (
              <div className="flex flex-wrap gap-1">
                {selected.slice(0, 8).map((ev) => {
                  const id = ev.identifier || ev.title;
                  return (
                    <button
                      key={id}
                      type="button"
                      className="px-1.5 py-0.5 rounded border border-edge text-slate-300 hover:border-accent/40"
                      onClick={() => {
                        appendChatDraftRef(`@${ev.title || id} `);
                        setOpen(false);
                      }}
                    >
                      @{String(ev.title || id).slice(0, 24)}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div>
            <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">对话技能</div>
            <div className="flex flex-wrap gap-1">
              {chatSkills.map((s) => {
                const on = selectedChatSkills.includes(s.id);
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => toggleSelectedChatSkill(s.id)}
                    className={`px-1.5 py-0.5 rounded border ${
                      on ? "border-accent text-accent" : "border-edge text-slate-400"
                    }`}
                  >
                    {s.icon} {s.id}
                  </button>
                );
              })}
              {chatSkills.length === 0 && <span className="text-slate-600">无已启用技能</span>}
            </div>
          </div>

          <div>
            <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">
              行动包（打开 Modal）
            </div>
            <div className="flex flex-wrap gap-1">
              {playbooks.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => {
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
                    setOpen(false);
                  }}
                  className="px-1.5 py-0.5 rounded border border-edge text-slate-400 hover:border-accent/40"
                >
                  {s.icon} {s.title}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">Connectors</div>
            <div className="flex flex-wrap gap-1">
              {connectors.map((c) => {
                const on = selectedConnectors.includes(c.id);
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => toggleSelectedConnector(c.id)}
                    className={`px-1.5 py-0.5 rounded border ${
                      on ? "border-emerald-500/50 text-emerald-300" : "border-edge text-slate-400"
                    }`}
                  >
                    {c.display_name}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
