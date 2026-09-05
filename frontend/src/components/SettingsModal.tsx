import { useCallback, useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import Modal from "./Modal";
import DependencyManager from "./DependencyManager";
import ApiSettingsPanel from "./ApiSettingsPanel";
import EnvFlagsPanel from "./EnvFlagsPanel";
import ParseProfileSelector from "./ParseProfileSelector";
import ApiAccessPanel, { isAuthError } from "./ApiAccessPanel";
import VisionModelPanel from "./VisionModelPanel";
import FormulationModeSelector from "./FormulationModeSelector";
import OcsrPanel from "./OcsrPanel";
import { useStore } from "../store";
import {
  api,
  formatApiError,
  type LLMModelOption,
  type LLMProviderInfo,
  type VisionSettings,
} from "../api";

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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [modelOptions, setModelOptions] = useState<LLMModelOption[]>([]);
  const [refreshingModels, setRefreshingModels] = useState(false);
  const [modelsRefreshHint, setModelsRefreshHint] = useState<string | null>(null);
  const [vision, setVision] = useState<VisionSettings | null>(null);

  const loadLlmSettings = useCallback(() => {
    setLoadError(null);
    return api
      .getSettings()
      .then((s) => {
        setProviders(s.providers ?? []);
        setKeySet(s.key_set);
        setVision(s.vision ?? null);
        setLlmConfig({
          provider: s.provider,
          model: s.model,
          baseUrl: s.base_url ?? undefined,
        });
      })
      .catch((e) => {
        setProviders([]);
        setVision(null);
        setLoadError(formatApiError(e));
      });
  }, [setLlmConfig]);

  useEffect(() => {
    if (!settingsOpen) return;
    setResult(null);
    setApiKeyDraft("");
    setModelsRefreshHint(null);
    void loadLlmSettings();
  }, [settingsOpen, reloadKey, loadLlmSettings]);

  const current = providers.find((p) => p.id === llmConfig.provider);

  useEffect(() => {
    setModelOptions(current?.models ?? []);
    setModelsRefreshHint(null);
  }, [current?.models, llmConfig.provider]);
  const showBaseUrl = !!current?.base_url || llmConfig.provider === "openai";

  function onProviderChange(provider: string) {
    const p = providers.find((x) => x.id === provider);
    const recommended = p?.models.find((m) => m.recommended) ?? p?.models[0];
    setModelOptions(p?.models ?? []);
    setModelsRefreshHint(null);
    setLlmConfig({
      provider,
      model: recommended?.id ?? "",
      // A custom endpoint has no catalog default, so clear the field rather
      // than carrying the previous provider's URL over to it.
      baseUrl: p?.base_url ?? undefined,
    });
    setResult(null);
  }

  async function onRefreshModels() {
    setRefreshingModels(true);
    setModelsRefreshHint(null);
    try {
      const res = await api.refreshLlmModels({
        provider: llmConfig.provider,
        baseUrl: llmConfig.baseUrl,
        model: llmConfig.model,
      });
      setModelOptions(res.models);
      setModelsRefreshHint(res.message);
      if (
        res.models.length > 0 &&
        !res.models.some((m) => m.id === llmConfig.model)
      ) {
        const next = res.models.find((m) => m.recommended) ?? res.models[0];
        setLlmConfig({ model: next.id });
      }
    } catch (e) {
      setModelsRefreshHint(formatApiError(e));
    } finally {
      setRefreshingModels(false);
    }
  }

  const models =
    modelOptions.length > 0
      ? modelOptions
      : llmConfig.model
        ? [{ id: llmConfig.model, label: llmConfig.model }]
        : [];

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
      setLoadError(null);
    } catch (e) {
      setResult({ ok: false, message: formatApiError(e) });
    } finally {
      setTesting(false);
    }
  }

  function onTokenSaved() {
    setReloadKey((k) => k + 1);
  }

  return (
    <Modal title="设置 · Settings" open={settingsOpen} onClose={toggleSettings} testId="modal-settings">
      <div className="flex gap-1 mb-4 border-b border-edge">
        {([
          ["llm", "大模型"],
          ["api", "API 配置"],
          ["env", "环境变量"],
          ["recommend", "推荐"],
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

      <ApiAccessPanel onTokenSaved={onTokenSaved} />

      {loadError && settingsTab === "llm" && (
        <div className="mb-3 text-xs rounded px-3 py-2 border border-rose-500/40 text-rose-400 bg-rose-500/10">
          无法加载大模型配置：{loadError}
          {isAuthError(loadError) && " — 请先在上方填写 API 访问令牌。"}
        </div>
      )}

      {settingsTab === "deps" ? (
        <DependencyManager reloadKey={reloadKey} />
      ) : settingsTab === "api" ? (
        <ApiSettingsPanel reloadKey={reloadKey} />
      ) : settingsTab === "env" ? (
        <EnvFlagsPanel reloadKey={reloadKey} />
      ) : settingsTab === "recommend" ? (
        <div className="space-y-4">
          <FormulationModeSelector />
          <ParseProfileSelector reloadKey={reloadKey} />
          <OcsrPanel />
        </div>
      ) : providers.length === 0 && !loadError ? (
        <p className="text-xs text-slate-500 py-4 text-center">正在加载供应商列表…</p>
      ) : (
        <div className="space-y-4">
          <p className="text-xs text-slate-500">
            文本任务与视觉任务分开配置，系统按任务类型自动调用对应模型。API Key 保存在服务器{" "}
            <code className="text-slate-400">.env</code> 中，也可在「API 配置」Tab 统一管理。
          </p>

          <div>
            <h3 className="text-xs uppercase tracking-widest text-accent2">文本模型 · Text</h3>
            <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
              研究问答、配方推荐、深度研究、意图解析等所有纯文本任务。
            </p>
          </div>

          <label className="block">
            <span className="text-xs text-slate-400">供应商 · Provider</span>
            <select
              value={llmConfig.provider}
              onChange={(e) => onProviderChange(e.target.value)}
              disabled={providers.length === 0}
              className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm disabled:opacity-50"
            >
              {providers.length === 0 ? (
                <option value={llmConfig.provider}>（无可用选项）</option>
              ) : (
                providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))
              )}
            </select>
          </label>

          <label className="block">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-slate-400">模型 · Model</span>
              <button
                type="button"
                onClick={() => void onRefreshModels()}
                disabled={refreshingModels || providers.length === 0}
                className="text-[10px] border border-edge text-slate-400 rounded px-2 py-0.5 hover:text-accent hover:border-accent/40 disabled:opacity-40"
              >
                {refreshingModels ? "更新中…" : "更新列表 ↻"}
              </button>
            </div>
            <select
              value={llmConfig.model}
              onChange={(e) => setLlmConfig({ model: e.target.value })}
              disabled={models.length === 0}
              className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm disabled:opacity-50"
            >
              {models.length === 0 ? (
                <option value={llmConfig.model}>（无可用选项）</option>
              ) : (
                models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                    {m.recommended ? " ⭐" : ""}
                  </option>
                ))
              )}
            </select>
            {modelsRefreshHint && (
              <p className={`text-[10px] mt-1 ${modelsRefreshHint.includes("失败") || modelsRefreshHint.includes("未配置") ? "text-amber-300/90" : "text-slate-500"}`}>
                {modelsRefreshHint}
              </p>
            )}
            {showBaseUrl && (
              <p className="text-[10px] text-slate-600 mt-1">
                修改 Base URL 后请点击「更新列表」同步远端可用模型。
              </p>
            )}
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

          {/* Each section saves to its own endpoint, so its button lives inside
              it. A shared save row between the two made the vision block read as
              an afterthought rather than the peer choice it is. */}
          <div className="flex justify-end pt-1">
            <button
              onClick={onSave}
              disabled={testing || providers.length === 0}
              className="text-sm bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-4 py-1.5 disabled:opacity-40"
            >
              {testing ? "测试中…" : "保存并测试连接"}
            </button>
          </div>

          <VisionModelPanel
            providers={providers}
            vision={vision}
            onSaved={() => void loadLlmSettings()}
          />

          <div className="flex justify-end border-t border-edge pt-3">
            <button
              onClick={toggleSettings}
              className="text-sm border border-edge text-slate-400 rounded px-4 py-1.5 hover:text-slate-200"
            >
              关闭
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
