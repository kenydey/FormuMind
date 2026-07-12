import { useEffect, useMemo, useState } from "react";
import { api, formatApiError, type EnvFlag } from "../api";

/** True/False toggle for one FORMUMIND_* environment variable. */
function FlagToggle({
  flag,
  draft,
  onChange,
}: {
  flag: EnvFlag;
  draft: boolean;
  onChange: (value: boolean) => void;
}) {
  const dirty = draft !== flag.value;
  const nonDefault = draft !== flag.default;
  return (
    <div
      className={`flex items-start justify-between gap-3 rounded border px-3 py-2 ${
        dirty ? "border-accent/50 bg-accent/5" : "border-edge/60"
      }`}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-slate-200">{flag.label}</span>
          <code className="text-[10px] text-slate-500">{flag.env_key}</code>
          {nonDefault && (
            <span className="text-[10px] px-1 py-px rounded bg-amber-500/10 border border-amber-500/30 text-amber-300">
              非默认
            </span>
          )}
        </div>
        <p className="text-[11px] text-slate-500 mt-0.5 leading-relaxed">
          {flag.description}
          {flag.hint && <span className="text-amber-400/80">（{flag.hint}）</span>}
        </p>
      </div>
      <div className="flex shrink-0 rounded border border-edge overflow-hidden text-xs">
        {([true, false] as const).map((v) => (
          <button
            key={String(v)}
            onClick={() => onChange(v)}
            className={`px-2.5 py-1 font-mono transition-colors ${
              draft === v
                ? v
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "bg-rose-500/20 text-rose-300"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            {v ? "True" : "False"}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function EnvFlagsPanel({ reloadKey = 0 }: { reloadKey?: number }) {
  const [flags, setFlags] = useState<EnvFlag[]>([]);
  const [drafts, setDrafts] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const r = await api.getEnvFlags();
      setFlags(r.flags ?? []);
      setDrafts(Object.fromEntries((r.flags ?? []).map((f) => [f.attr, f.value])));
    } catch (e) {
      setFlags([]);
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  const groups = useMemo(() => {
    const map = new Map<string, { label: string; items: EnvFlag[] }>();
    for (const f of flags) {
      if (!map.has(f.category)) map.set(f.category, { label: f.category_label, items: [] });
      map.get(f.category)!.items.push(f);
    }
    return [...map.values()];
  }, [flags]);

  const dirtyCount = flags.filter((f) => drafts[f.attr] !== f.value).length;

  async function onSave() {
    const updates = Object.fromEntries(
      flags.filter((f) => drafts[f.attr] !== f.value).map((f) => [f.attr, drafts[f.attr]]),
    );
    if (Object.keys(updates).length === 0) return;
    setSaving(true);
    setSavedMsg(null);
    setError(null);
    try {
      const r = await api.postEnvFlags(updates);
      setFlags(r.flags ?? []);
      setDrafts(Object.fromEntries((r.flags ?? []).map((f) => [f.attr, f.value])));
      setSavedMsg(`已更新 ${r.updated.length} 项 — 写入 .env 并即时生效`);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setSaving(false);
    }
  }

  function onResetDefaults() {
    setDrafts(Object.fromEntries(flags.map((f) => [f.attr, f.default])));
    setSavedMsg(null);
  }

  if (loading && flags.length === 0) {
    return <p className="text-xs text-slate-500 py-4 text-center">正在加载环境变量…</p>;
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        以下功能开关对应服务器的 <code className="text-slate-400">FORMUMIND_*</code> 环境变量。
        保存后写入进程环境与 <code className="text-slate-400">.env</code> 文件，
        对后续请求<span className="text-slate-300">即时生效</span>并在重启后保持
        （个别项需重启，见括号提示）。
      </p>

      {error && (
        <div className="text-xs rounded px-3 py-2 border border-rose-500/40 text-rose-400 bg-rose-500/10">
          {error}
        </div>
      )}
      {savedMsg && (
        <div className="text-xs rounded px-3 py-2 border border-emerald-500/40 text-emerald-400 bg-emerald-500/10">
          ✓ {savedMsg}
        </div>
      )}

      <div className="max-h-[52vh] overflow-y-auto pr-1 space-y-4">
        {groups.map((g) => (
          <div key={g.label}>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1.5">
              {g.label}
            </div>
            <div className="space-y-1.5">
              {g.items.map((f) => (
                <FlagToggle
                  key={f.attr}
                  flag={f}
                  draft={drafts[f.attr] ?? f.value}
                  onChange={(v) => {
                    setDrafts((prev) => ({ ...prev, [f.attr]: v }));
                    setSavedMsg(null);
                  }}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between pt-1">
        <button
          onClick={onResetDefaults}
          className="text-xs border border-edge text-slate-400 rounded px-3 py-1.5 hover:text-slate-200"
        >
          恢复默认值
        </button>
        <div className="flex items-center gap-2">
          {dirtyCount > 0 && (
            <span className="text-[11px] text-amber-400">{dirtyCount} 项未保存</span>
          )}
          <button
            onClick={onSave}
            disabled={saving || dirtyCount === 0}
            className="text-sm bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-4 py-1.5 disabled:opacity-40"
          >
            {saving ? "保存中…" : "保存并生效"}
          </button>
        </div>
      </div>
    </div>
  );
}
