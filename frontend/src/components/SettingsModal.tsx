import { useEffect, useState } from "react";
import Modal from "./Modal";
import DependencyManager from "./DependencyManager";
import { useStore } from "../store";
import { api, type LLMProviderInfo } from "../api";

export default function SettingsModal() {
  const { settingsOpen, toggleSettings, llmConfig, setLlmConfig, settingsTab, setSettingsTab } =
    useStore();
  const [providers, setProviders] = useState<LLMProviderInfo[]>([]);
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  // Load provider catalog from the backend when the modal opens.
  useEffect(() => {
    if (!settingsOpen) return;
    setResult(null);
    api
      .getSettings()
      .then((s) => setProviders(s.providers))
      .catch(() => setProviders([]));
  }, [settingsOpen]);

  const current = providers.find((p) => p.id === llmConfig.provider);
  const models = current?.models ?? [];
  // A custom base URL field is relevant only for OpenAI-compatible providers.
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
      await api.postSettings({
        provider: llmConfig.provider,
        model: llmConfig.model,
        api_key: llmConfig.apiKey || undefined,
        baseUrl: llmConfig.baseUrl,
      });
      const t = await api.testConnection();
      setResult({ ok: t.ok, message: t.message });
    } catch (e) {
      setResult({ ok: false, message: String(e) });
    } finally {
      setTesting(false);
    }
  }

  return (
    <Modal title="设置 · Settings" open={settingsOpen} onClose={toggleSettings}>
      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-edge">
        {([
          ["llm", "大模型"],
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
      ) : (
      <div className="space-y-4">
        <p className="text-xs text-slate-500">
          配置用于研究问答与配方综述的大语言模型。API Key 仅在本次会话内同步到后端，浏览器使用 localStorage 持久化保存。
        </p>

        {/* Provider */}
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

        {/* Model */}
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

        {/* API key */}
        <label className="block">
          <span className="text-xs text-slate-400">API Key</span>
          <div className="flex gap-2 mt-1">
            <input
              type={showKey ? "text" : "password"}
              value={llmConfig.apiKey}
              onChange={(e) => setLlmConfig({ apiKey: e.target.value })}
              placeholder="sk-..."
              className="flex-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
            />
            <button
              onClick={() => setShowKey((v) => !v)}
              className="text-xs border border-edge text-slate-400 rounded px-2.5 hover:text-accent hover:border-accent/40"
            >
              {showKey ? "隐藏" : "显示"}
            </button>
          </div>
        </label>

        {/* Custom base URL */}
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

        {/* Result */}
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

        {/* Actions */}
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
