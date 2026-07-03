import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import Modal from "./Modal";
import DependencyManager from "./DependencyManager";
import ApiSettingsPanel from "./ApiSettingsPanel";
import { useStore } from "../store";
import { api, type LLMProviderInfo } from "../api";

export default function SettingsModal() {
  const { settingsOpen, toggleSettings, llmConfig, setLlmConfig, settingsTab, setSettingsTab } =
    useStore(
      useShallow((s) => ({
        settingsOpen: s.settingsOpen,
        toggleSettings: s.toggleSettings,
        llmConfig: s.llmConfig,
        setLlmConfig: s.setLlmConfig,
        settingsTab: s.settingsTab,
        setSettingsTab: s.setSettingsTab,
      }))
    );
  const [providers, setProviders] = useState<LLMProviderInfo[]>([]);
  const [keySet, setKeySet] = useState(false);
  const [apiKeyDraft, setApiKeyDraft] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (!settingsOpen) return;
    setResult(null);
    setApiKeyDraft("");
    api
      .getSettings()
      .then((s) => {
        setProviders(s.providers);
        setKeySet(s.key_set);
        setLlmConfig({
          provider: s.provider,
          model: s.model,
          baseUrl: s.base_url ?? undefined,
        });
      })
      .catch(() => setProviders([]));
  }, [settingsOpen, setLlmConfig]);

  const current = providers.find((p) => p.id === llmConfig.provider);
  const models = current?.models ?? [];
  const showBaseUrl = !!current?.base_url || llmConfig.provider === "openai";

  function onProviderChange(provider: string) {
    const p = providers.find((x) => x.id === provider);
    const recommended = p?.models.find((m) => m.recommended) ?? p?.models[0];
    setLlmConfig({
      provider,
      model: recommended?.id ?? "",
      baseUrl: p?.base_url,
    });
    setResult(null);
  }

  async function onSave() {
    setTesting(true);
    setResult(null);
    try {
      const t = await api.postSettings({
        provider: llmConfig.provider,
        model: llmConfig.model,
        api_key: apiKeyDraft.trim() || undefined,
        baseUrl: llmConfig.baseUrl,
      });
      setKeySet(t.ok || keySet || !!apiKeyDraft.trim());
      setApiKeyDraft("");
      setResult({ ok: t.ok, message: t.message });
    } catch (e) {
      setResult({ ok: false, message: String(e) });
    } finally {
      setTesting(false);
    }
  }

  return (
    <Modal title="设置 · Settings" open={settingsOpen} onClose={toggleSettings}>
      <div className="flex gap-1 mb-4 border-b border-edge">
        {([
          ["llm", "大模型"],
          ["api", "API 配置"],
          ["deps", "依赖管理"],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setSettingsTab(id)}
            className={`text-sm px-3 py-1.5 -mb-px border-b-2 transition-colors ${
              settingsTab === id
                ? "border-accent text-accent"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {settingsTab === "deps" ? (
        <DependencyManager />
      ) : settingsTab === "api" ? (
        <ApiSettingsPanel />
      ) : (
        <div className="space-y-4">
          <p className="text-xs text-slate-500">
            配置用于研究问答与配方综述的大语言模型。API Key 保存在服务器{" "}
            <code className="text-slate-400">.env</code> 中，也可在「API 配置」Tab 统一管理。
          </p>

          <label className="block">
            <span className="text-xs text-slate-400">供应商 · Provider</span>
            <select
              value={llmConfig.provider}
              onChange={(e) => onProviderChange(e.target.value)}
              className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm"
            >
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs text-slate-400">模型 · Model</span>
            <select
              value={llmConfig.model}
              onChange={(e) => setLlmConfig({ model: e.target.value })}
              className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm"
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                  {m.recommended ? " ⭐" : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs text-slate-400">API Key</span>
            <div className="flex gap-2 mt-1">
              <input
                type={showKey ? "text" : "password"}
                value={apiKeyDraft}
                onChange={(e) => setApiKeyDraft(e.target.value)}
                placeholder={keySet ? "已配置 — 输入新密钥以覆盖" : "sk-..."}
                className="flex-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
              />
              <button
                onClick={() => setShowKey((v) => !v)}
                className="text-xs border border-edge text-slate-400 rounded px-2.5 hover:text-accent hover:border-accent/40"
              >
                {showKey ? "隐藏" : "显示"}
              </button>
            </div>
            {keySet && !apiKeyDraft && (
              <p className="text-[10px] text-emerald-500/80 mt-1">当前供应商密钥已写入 .env</p>
            )}
          </label>

          {showBaseUrl && (
            <label className="block">
              <span className="text-xs text-slate-400">自定义 Base URL（可选）</span>
              <input
                type="text"
                value={llmConfig.baseUrl ?? ""}
                onChange={(e) => setLlmConfig({ baseUrl: e.target.value })}
                placeholder={current?.base_url ?? "https://..."}
                className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
              />
            </label>
          )}

          {result && (
            <div
              className={`text-xs rounded px-3 py-2 border ${
                result.ok
                  ? "border-emerald-500/40 text-emerald-400 bg-emerald-500/10"
                  : "border-rose-500/40 text-rose-400 bg-rose-500/10"
              }`}
            >
              {result.ok ? "✓ " : "✗ "}
              {result.message}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={toggleSettings}
              className="text-sm border border-edge text-slate-400 rounded px-4 py-1.5 hover:text-slate-200"
            >
              关闭
            </button>
            <button
              onClick={onSave}
              disabled={testing}
              className="text-sm bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-4 py-1.5 disabled:opacity-40"
            >
              {testing ? "测试中…" : "保存并测试连接"}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
