import { useCallback, useEffect, useState } from "react";
import {
  api,
  formatApiError,
  type NotebookLMLoginResult,
  type NotebookLMStatus,
} from "../api";
import { useStore } from "../store";

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block w-1.5 h-1.5 rounded-full ${
        ok ? "bg-emerald-400" : "bg-slate-500"
      }`}
      aria-hidden
    />
  );
}

export default function NotebookLMPanel({ reloadKey = 0 }: { reloadKey?: number }) {
  const openSettings = useStore((s) => s.openSettings);
  const [status, setStatus] = useState<NotebookLMStatus | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [notebookId, setNotebookId] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loginBusy, setLoginBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loginResult, setLoginResult] = useState<NotebookLMLoginResult | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await api.notebooklmStatus();
      setStatus(s);
      setEnabled(Boolean(s.enabled));
      setNotebookId(s.notebook_id ?? "");
    } catch (e) {
      setStatus(null);
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [reloadKey, refresh]);

  async function onSave() {
    setSaving(true);
    setError(null);
    setLoginResult(null);
    try {
      const s = await api.notebooklmConfig({
        enabled,
        notebook_id: notebookId.trim(),
      });
      setStatus(s);
      setEnabled(Boolean(s.enabled));
      setNotebookId(s.notebook_id ?? "");
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setSaving(false);
    }
  }

  async function onLogin() {
    setLoginBusy(true);
    setError(null);
    setLoginResult(null);
    try {
      const res = await api.notebooklmLogin();
      setLoginResult(res);
      await refresh();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoginBusy(false);
    }
  }

  const checks = [
    { ok: Boolean(status?.lib_installed), label: "notebooklm-py 已安装" },
    { ok: Boolean(status?.enabled), label: "功能已启用" },
    { ok: Boolean(status?.session_present), label: "Google 会话已授权" },
  ];

  return (
    <div className="space-y-4" data-testid="notebooklm-panel">
      <div>
        <h3 className="text-xs uppercase tracking-widest text-accent2">NotebookLM · 授权</h3>
        <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
          全局仅负责启用 NotebookLM 与 Google 授权。每个研究项目的 Notebook ID 请在左栏勾选
          NotebookLM 时于项目弹窗中配置（按项目绑定）。
        </p>
      </div>

      {loading && !status && (
        <p className="text-xs text-slate-500 py-2 text-center">加载状态…</p>
      )}

      {error && (
        <div className="text-xs rounded px-3 py-2 border border-rose-500/40 text-rose-400 bg-rose-500/10">
          {error}
        </div>
      )}

      {status && (
        <>
          <div
            className={`rounded border px-3 py-2 text-xs ${
              status.available
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-amber-500/40 bg-amber-500/10 text-amber-200"
            }`}
          >
            {status.available
              ? "✓ 全局授权已就绪；请在各项目中绑定 Notebook ID"
              : `⚠ 未就绪${status.hint ? ` — ${status.hint}` : ""}`}
          </div>

          <ul className="space-y-1.5">
            {checks.map((c) => (
              <li key={c.label} className="flex items-center gap-2 text-xs text-slate-300">
                <StatusDot ok={c.ok} />
                <span className={c.ok ? "text-slate-300" : "text-slate-500"}>{c.label}</span>
              </li>
            ))}
          </ul>

          {!status.lib_installed && (
            <button
              type="button"
              onClick={() => openSettings("deps")}
              className="text-xs border border-accent/40 text-accent rounded px-2.5 py-1.5 hover:bg-accent/10"
            >
              去依赖管理安装 notebooklm-py →
            </button>
          )}

          <label className="flex items-center justify-between gap-3 rounded border border-edge px-3 py-2">
            <span className="text-sm text-slate-200">启用 NotebookLM</span>
            <button
              type="button"
              role="switch"
              aria-checked={enabled}
              onClick={() => setEnabled((v) => !v)}
              className={`relative w-10 h-5 rounded-full transition-colors ${
                enabled ? "bg-accent/80" : "bg-edge"
              }`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                  enabled ? "translate-x-5" : ""
                }`}
              />
            </button>
          </label>

          <label className="block">
            <span className="text-xs text-slate-400">遗留全局 Notebook ID（可选，仅作迁移回退）</span>
            <input
              value={notebookId}
              onChange={(e) => setNotebookId(e.target.value)}
              placeholder="新项目请在左栏项目弹窗中配置"
              className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
              data-testid="notebooklm-notebook-id"
            />
            <span className="text-[10px] text-slate-500 mt-1 block">
              推荐：在信息类别勾选 NotebookLM 时为每个项目单独设置 ID。
            </span>
          </label>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void onSave()}
              disabled={saving}
              className="text-sm bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-1.5 disabled:opacity-40"
            >
              {saving ? "保存中…" : "保存配置"}
            </button>
            <button
              type="button"
              onClick={() => void onLogin()}
              disabled={loginBusy || !status.lib_installed}
              className="text-sm border border-accent2 text-accent2 hover:bg-accent2/10 rounded px-3 py-1.5 disabled:opacity-40"
              data-testid="notebooklm-login"
            >
              {loginBusy ? "启动中…" : "授权登录"}
            </button>
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              className="text-sm border border-edge text-slate-400 hover:text-slate-200 rounded px-3 py-1.5 disabled:opacity-40"
            >
              刷新状态
            </button>
          </div>

          {loginResult && (
            <div className="text-xs rounded px-3 py-2 border border-edge bg-ink/40 space-y-1">
              <p className="text-slate-300">
                {loginResult.started
                  ? "已尝试在后端打开浏览器登录窗口。"
                  : "需手动完成授权："}
              </p>
              {loginResult.hint && <p className="text-amber-200/90">{loginResult.hint}</p>}
              {loginResult.command && (
                <p className="font-mono text-accent2 text-[11px]">{loginResult.command}</p>
              )}
              {loginResult.manual_url && (
                <a
                  href={loginResult.manual_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  打开 NotebookLM
                </a>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
