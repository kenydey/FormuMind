import { useEffect, useState } from "react";
import { api } from "../api";

interface ModeChoice {
  value: string;
  label: string;
  desc: string;
}

type Props = {
  /** Compact inline control for Chat toolbar (S1). */
  compact?: boolean;
  className?: string;
};

export default function WikiChatModeSelector({ compact = false, className }: Props) {
  const [current, setCurrent] = useState("balanced");
  const [choices, setChoices] = useState<ModeChoice[]>([]);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [available, setAvailable] = useState(true);

  useEffect(() => {
    api
      .getWikiChatMode()
      .then((r) => {
        setCurrent(r.current);
        setChoices(r.choices);
        setAvailable(true);
      })
      .catch(() => setAvailable(false));
  }, []);

  const handleChange = async (mode: string) => {
    setSaving(true);
    setStatus(null);
    try {
      await api.setWikiChatMode(mode);
      setCurrent(mode);
      setStatus("已更新（下次问答生效）");
    } catch {
      setStatus("更新失败");
    } finally {
      setSaving(false);
    }
  };

  if (!available) return null;

  if (compact) {
    return (
      <div
        className={`flex flex-wrap items-center gap-1 text-[10px] ${className || ""}`}
        data-testid="wiki-chat-mode-compact"
        title="Wiki / Raw 融合模式（不影响 Claims 只认 Raw）"
      >
        <span className="text-slate-500 shrink-0">Wiki 模式</span>
        {choices.map((c) => (
          <button
            key={c.value}
            type="button"
            disabled={saving}
            onClick={() => void handleChange(c.value)}
            className={`px-1.5 py-0.5 rounded border ${
              current === c.value
                ? "border-accent/50 bg-accent/10 text-accent"
                : "border-edge/60 text-slate-400 hover:border-edge"
            }`}
            title={c.desc}
          >
            {c.label}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className={`border border-edge/60 rounded p-3 bg-panel/20 ${className || ""}`}>
      <h3 className="text-sm text-slate-200 mb-2">Wiki 问答融合模式</h3>
      <p className="text-[10px] text-slate-500 mb-3">
        控制 Chat 双轨中 Wiki 与 Raw 的权重。Claims / 引用证据链仍只认 Raw。
        对应 <code className="text-accent2/80">FORMUMIND_WIKI_CHAT_MODE</code>
      </p>
      <div className="space-y-2">
        {choices.map((c) => (
          <label
            key={c.value}
            className={`flex items-start gap-3 rounded border px-3 py-2 cursor-pointer transition-colors ${
              current === c.value
                ? "border-accent/50 bg-accent/5"
                : "border-edge/60 hover:border-edge"
            }`}
          >
            <input
              type="radio"
              name="wiki_chat_mode"
              value={c.value}
              checked={current === c.value}
              onChange={() => void handleChange(c.value)}
              disabled={saving}
              className="mt-0.5"
            />
            <div>
              <span className="text-sm text-slate-200">{c.label}</span>
              <p className="text-[11px] text-slate-500 leading-relaxed">{c.desc}</p>
            </div>
          </label>
        ))}
      </div>
      {status && (
        <p
          className={`text-[10px] mt-2 ${
            status.includes("失败") ? "text-rose-400" : "text-emerald-400"
          }`}
        >
          {status}
        </p>
      )}
    </div>
  );
}
