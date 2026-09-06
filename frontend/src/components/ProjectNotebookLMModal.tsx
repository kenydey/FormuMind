import { useCallback, useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  api,
  formatApiError,
  type NotebookLMLoginResult,
  type NotebookLMStatus,
} from "../api";
import { useStore } from "../store";
import Modal from "./Modal";

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

/**
 * Per-project NotebookLM binder.
 *
 * Global Settings keep auth (enable + Google login). Each research project stores
 * its own notebook id on the workspace; this modal opens when NotebookLM is first
 * checked under 信息类别.
 */
export default function ProjectNotebookLMModal() {
  const {
    openModal,
    setOpenModal,
    notebooklmNotebookId,
    setNotebooklmNotebookId,
    openSettings,
  } = useStore(
    useShallow((s) => ({
      openModal: s.openModal,
      setOpenModal: s.setOpenModal,
      notebooklmNotebookId: s.notebooklmNotebookId,
      setNotebooklmNotebookId: s.setNotebooklmNotebookId,
      openSettings: s.openSettings,
    }))
  );
  const open = openModal === "notebooklm-setup";
  const [status, setStatus] = useState<NotebookLMStatus | null>(null);
  const [draftId, setDraftId] = useState("");
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
    } catch (e) {
      setStatus(null);
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setDraftId(notebooklmNotebookId);
    setLoginResult(null);
    void refresh();
  }, [open, notebooklmNotebookId, refresh]);

  async function onSave() {
    const id = draftId.trim();
    if (!id) {
      setError("请填写本项目的 Notebook ID");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (status && !status.enabled) {
        await api.notebooklmConfig({ enabled: true });
      }
      setNotebooklmNotebookId(id);
      setOpenModal(null);
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

  function applyLegacyGlobal() {
    const legacy = (status?.notebook_id ?? "").trim();
    if (legacy) setDraftId(legacy);
  }

  const authReady = Boolean(status?.auth_ready ?? status?.available);
  const checks = [
    { ok: Boolean(status?.lib_installed), label: "notebooklm-py 已安装" },
    { ok: Boolean(status?.enabled), label: "功能已启用" },
    { ok: Boolean(status?.session_present), label: "Google 会话已授权" },
    { ok: Boolean(draftId.trim()), label: "本项目 Notebook ID 已填写" },
  ];

  return (
    <Modal
      title="项目 NotebookLM 设置"
      open={open}
      onClose={() => setOpenModal(null)}
      size="md"
      testId="modal-notebooklm-setup"
      onSave={() => void onSave()}
      saveLabel={saving ? "保存中…" : "保存到本项目"}
    >
      <div className="space-y-4" data-testid="project-notebooklm-modal">
        <p className="text-[11px] text-slate-500 leading-relaxed">
          每个研究项目关联独立的 NotebookLM 笔记本。全局设置只负责启用与 Google
          授权；此处填写的 Notebook ID 会随项目工作区一起保存。
        </p>

        {loading && !status && (
          <p className="text-xs text-slate-500 py-2 text-center">加载全局授权状态…</p>
        )}

        {error && (
          <div className="text-xs rounded px-3 py-2 border border-rose-500/40 text-rose-400 bg-rose-500/10">
            {error}
          </div>
        )}

        {status && (
          <div
            className={`rounded border px-3 py-2 text-xs ${
              authReady
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-amber-500/40 bg-amber-500/10 text-amber-200"
            }`}
          >
            {authReady
              ? "✓ 全局授权已就绪，请绑定本项目的 Notebook ID"
              : `⚠ 全局未就绪${status.hint ? ` — ${status.hint}` : ""}`}
          </div>
        )}

        <ul className="space-y-1.5">
          {checks.map((c) => (
            <li key={c.label} className="flex items-center gap-2 text-xs text-slate-300">
              <StatusDot ok={c.ok} />
              <span className={c.ok ? "text-slate-300" : "text-slate-500"}>{c.label}</span>
            </li>
          ))}
        </ul>

        {status && !status.lib_installed && (
          <button
            type="button"
            onClick={() => {
              setOpenModal(null);
              openSettings("deps");
            }}
            className="text-xs border border-accent/40 text-accent rounded px-2.5 py-1.5 hover:bg-accent/10"
          >
            去依赖管理安装 notebooklm-py →
          </button>
        )}

        <label className="block">
          <span className="text-xs text-slate-400">本项目 Notebook ID</span>
          <input
            value={draftId}
            onChange={(e) => setDraftId(e.target.value)}
            placeholder="NotebookLM 链接中的 notebook id"
            className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
            data-testid="project-notebooklm-id"
          />
        </label>

        {Boolean(status?.notebook_id) && status?.notebook_id !== draftId.trim() && (
          <button
            type="button"
            onClick={applyLegacyGlobal}
            className="text-[11px] text-accent2 underline"
          >
            使用全局遗留 Notebook ID（{status?.notebook_id}）
          </button>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void onLogin()}
            disabled={loginBusy || !status?.lib_installed}
            className="text-sm border border-accent2 text-accent2 hover:bg-accent2/10 rounded px-3 py-1.5 disabled:opacity-40"
            data-testid="project-notebooklm-login"
          >
            {loginBusy ? "启动中…" : "授权登录（全局）"}
          </button>
          <button
            type="button"
            onClick={() => {
              setOpenModal(null);
              openSettings("notebooklm");
            }}
            className="text-sm border border-edge text-slate-400 hover:text-slate-200 rounded px-3 py-1.5"
          >
            打开全局 NotebookLM 设置
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
      </div>
    </Modal>
  );
}
