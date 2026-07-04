import { useEffect, useState } from "react";
import { api, type SecretStatus } from "../api";

const GROUP_LABELS: Record<string, string> = {
  llm: "大模型",
  search: "检索增强",
  patent: "专利数据源",
  research: "学术 / 研究",
  infra: "基础设施",
};

export default function ApiSettingsPanel({ reloadKey = 0 }: { reloadKey?: number }) {
  const [secrets, setSecrets] = useState<SecretStatus[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ id: string; ok: boolean; text: string } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  async function refresh() {
    setLoadError(null);
    try {
      const res = await api.getSecrets();
      setSecrets(res.secrets ?? []);
    } catch (e) {
      setSecrets([]);
      setLoadError(String(e));
    }
  }

  useEffect(() => {
    void refresh();
  }, [reloadKey]);

  const groups = secrets.reduce<Record<string, SecretStatus[]>>((acc, s) => {
    (acc[s.group] ||= []).push(s);
    return acc;
  }, {});

  function startEdit(id: string) {
    setEditing((prev) => new Set(prev).add(id));
    setDrafts((d) => ({ ...d, [id]: "" }));
  }

  async function saveOne(id: string) {
    setBusy(id);
    setMessage(null);
    try {
      const value = drafts[id] ?? "";
      await api.postSecrets({ [id]: value });
      setEditing((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      setDrafts((d) => {
        const { [id]: _, ...rest } = d;
        return rest;
      });
      await refresh();
      setMessage({ id, ok: true, text: "已保存到 .env" });
    } catch (e) {
      setMessage({ id, ok: false, text: String(e) });
    } finally {
      setBusy(null);
    }
  }

  async function testOne(id: string) {
    setBusy(`test-${id}`);
    setMessage(null);
    try {
      const res = await api.testSecret(id);
      setMessage({ id, ok: res.ok, text: res.message });
    } catch (e) {
      setMessage({ id, ok: false, text: String(e) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5 max-h-[60vh] overflow-y-auto pr-1">
      <p className="text-xs text-slate-500">
        API 密钥保存在服务器 <code className="text-slate-400">.env</code> 文件中。修改后点击「保存」即时生效；Celery
        Worker 需重启后读取新密钥。
      </p>

      {loadError && (
        <div className="text-xs rounded px-3 py-2 border border-rose-500/40 text-rose-400 bg-rose-500/10">
          无法加载 API 配置：{loadError}
        </div>
      )}

      {secrets.length === 0 && !loadError && (
        <p className="text-xs text-slate-500 py-2">正在加载密钥列表…</p>
      )}

      {Object.entries(groups).map(([group, items]) => (
        <section key={group}>
          <h3 className="text-xs uppercase tracking-widest text-accent2 mb-2">
            {GROUP_LABELS[group] ?? group}
          </h3>
          <div className="space-y-2">
            {items.map((s) => {
              const isEditing = editing.has(s.id);
              return (
                <div
                  key={s.id}
                  className="rounded-lg border border-edge bg-ink/40 px-3 py-2.5 flex flex-col gap-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-sm text-slate-200 truncate">{s.label}</div>
                      <div className="text-[10px] text-slate-500 font-mono truncate">{s.env_key}</div>
                    </div>
                    <span
                      className={`text-[10px] shrink-0 px-1.5 py-0.5 rounded border ${
                        s.set
                          ? "border-emerald-500/40 text-emerald-400"
                          : "border-slate-600 text-slate-500"
                      }`}
                    >
                      {s.set ? "已配置" : "未配置"}
                    </span>
                  </div>

                  {isEditing ? (
                    <input
                      type="password"
                      autoFocus
                      placeholder={s.masked ? `当前 ${s.masked}` : "输入新密钥"}
                      value={drafts[s.id] ?? ""}
                      onChange={(e) => setDrafts((d) => ({ ...d, [s.id]: e.target.value }))}
                      className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
                    />
                  ) : (
                    <div className="text-xs font-mono text-slate-400">{s.masked || "—"}</div>
                  )}

                  <div className="flex gap-2 justify-end">
                    {!isEditing ? (
                      <button
                        type="button"
                        onClick={() => startEdit(s.id)}
                        className="text-xs border border-edge text-slate-400 rounded px-2.5 py-1 hover:text-accent"
                      >
                        修改
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => saveOne(s.id)}
                        disabled={busy === s.id}
                        className="text-xs bg-accent/90 text-ink font-semibold rounded px-2.5 py-1 disabled:opacity-40"
                      >
                        {busy === s.id ? "保存中…" : "保存"}
                      </button>
                    )}
                    {(s.group === "search" || s.group === "patent") && s.set && (
                      <button
                        type="button"
                        onClick={() => testOne(s.id)}
                        disabled={busy === `test-${s.id}`}
                        className="text-xs border border-edge text-slate-400 rounded px-2.5 py-1 hover:text-accent"
                      >
                        测试
                      </button>
                    )}
                  </div>

                  {message?.id === s.id && (
                    <div
                      className={`text-[11px] ${message.ok ? "text-emerald-400" : "text-rose-400"}`}
                    >
                      {message.text}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
