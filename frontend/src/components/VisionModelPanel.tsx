/**
 * The vision model, configured separately from the text model.
 *
 * These were one setting, and that was actively harmful: vision borrowed the
 * chat provider's key and base_url, so choosing DeepSeek for text — a perfectly
 * reasonable choice — silently disabled every chemical structure and figure in
 * the knowledge base, with nothing on screen to say so.
 *
 * The provider list's first entry is "follow the text model" (an empty value),
 * which is the default and keeps the old behaviour for anyone who does not care.
 *
 * A custom endpoint gets a free-text model field rather than a dropdown, because
 * the name is whatever the user deployed: TGI wants the literal string `tgi`,
 * vLLM wants a repo id. "更新列表 ↻" discovers it from the endpoint itself.
 */
import { useEffect, useState } from "react";
import {
  api,
  formatApiError,
  type LLMModelOption,
  type LLMProviderInfo,
  type VisionProbeResult,
  type VisionSettings,
} from "../api";

/** Empty provider == inherit the text role. */
const INHERIT = "";
export const CUSTOM_PROVIDER = "custom";

interface Props {
  providers: LLMProviderInfo[];
  vision: VisionSettings | null;
  /** Re-fetch /api/settings so the parent's view matches what we just saved. */
  onSaved: () => void;
}

export default function VisionModelPanel({ providers, vision, onSaved }: Props) {
  const [provider, setProvider] = useState(INHERIT);
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKeyDraft, setApiKeyDraft] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [modelOptions, setModelOptions] = useState<LLMModelOption[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshHint, setRefreshHint] = useState<string | null>(null);

  // Server state is the source of truth; mirror it into the draft on load.
  //
  // Deliberately does NOT clear `result`: saving calls onSaved(), which refetches
  // and lands here, so clearing would wipe the confirmation banner the save just
  // set and make a successful save look like nothing happened. Stale banners are
  // not a risk — Modal unmounts this panel when it closes.
  useEffect(() => {
    if (!vision) return;
    setProvider(vision.provider ?? INHERIT);
    setModel(vision.inherits ? "" : vision.model ?? "");
    setBaseUrl(vision.base_url ?? "");
    setApiKeyDraft("");
    setRefreshHint(null);
  }, [vision]);

  const inheriting = provider === INHERIT;
  const isCustom = provider === CUSTOM_PROVIDER;
  const current = providers.find((p) => p.id === provider);

  useEffect(() => {
    setModelOptions(current?.models ?? []);
    setRefreshHint(null);
  }, [current?.models, provider]);

  function onProviderChange(next: string) {
    setProvider(next);
    setResult(null);
    setRefreshHint(null);
    if (next === INHERIT) {
      setModel("");
      setBaseUrl("");
      return;
    }
    const p = providers.find((x) => x.id === next);
    // A custom endpoint has no catalog to recommend from — leave it to the user.
    const recommended = p?.models.find((m) => m.recommended) ?? p?.models[0];
    setModel(recommended?.id ?? "");
    setBaseUrl(p?.base_url ?? "");
  }

  async function onRefreshModels() {
    setRefreshing(true);
    setRefreshHint(null);
    try {
      const res = await api.refreshLlmModels({ provider, baseUrl, model });
      setModelOptions(res.models);
      setRefreshHint(res.message);
      if (res.models.length > 0 && !res.models.some((m) => m.id === model)) {
        setModel((res.models.find((m) => m.recommended) ?? res.models[0]).id);
      }
    } catch (e) {
      setRefreshHint(formatApiError(e));
    } finally {
      setRefreshing(false);
    }
  }

  async function onSave() {
    setSaving(true);
    setResult(null);
    try {
      await api.postVisionSettings({
        provider,
        model: inheriting ? "" : model,
        api_key: apiKeyDraft.trim() || undefined,
        baseUrl: inheriting ? "" : baseUrl,
      });
      setApiKeyDraft("");
      setResult({
        ok: true,
        message: inheriting ? "已改为跟随文本模型" : "已保存（点「测试视觉」验证是否真的能读图）",
      });
      onSaved();
    } catch (e) {
      setResult({ ok: false, message: formatApiError(e) });
    } finally {
      setSaving(false);
    }
  }

  async function onProbe() {
    setProbing(true);
    setResult(null);
    try {
      const r: VisionProbeResult = await api.testVision();
      setResult({ ok: r.ok, message: r.message });
    } catch (e) {
      setResult({ ok: false, message: formatApiError(e) });
    } finally {
      setProbing(false);
    }
  }

  const models =
    modelOptions.length > 0
      ? modelOptions
      : model
        ? [{ id: model, label: model }]
        : [];

  return (
    <div className="space-y-3 border-t border-edge pt-4">
      <div>
        <h3 className="text-xs uppercase tracking-widest text-accent2">视觉模型 · Vision</h3>
        <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
          化学结构图、图表、PDF 中未能渲染的表格、以及上传的图片。可与文本模型选不同的
          供应商——很多强文本模型（如 DeepSeek）不接受图片输入，用它做视觉会让图表降级为
          占位说明。
        </p>
      </div>

      <label className="block">
        <span className="text-xs text-slate-400">供应商 · Provider</span>
        <select
          value={provider}
          onChange={(e) => onProviderChange(e.target.value)}
          aria-label="视觉模型供应商"
          className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm"
        >
          <option value={INHERIT}>跟随文本模型</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </label>

      {inheriting ? (
        <p className="text-[11px] text-slate-500">
          当前与文本模型共用同一供应商与密钥
          {vision?.model ? <>（模型 <code className="text-slate-400">{vision.model}</code>）</> : null}
          。若该模型不支持图片，请在上方另选一个供应商。
        </p>
      ) : (
        <>
          <label className="block">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-slate-400">模型 · Model</span>
              <button
                type="button"
                onClick={() => void onRefreshModels()}
                disabled={refreshing}
                aria-label="更新视觉模型列表"
                className="text-[10px] border border-edge text-slate-400 rounded px-2 py-0.5 hover:text-accent hover:border-accent/40 disabled:opacity-40"
              >
                {refreshing ? "更新中…" : "更新列表 ↻"}
              </button>
            </div>
            {isCustom ? (
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="tgi（TGI 占位符）或 Qwen/Qwen2.5-VL-7B-Instruct（vLLM）"
                aria-label="视觉模型名称"
                className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
              />
            ) : (
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={models.length === 0}
                aria-label="视觉模型名称"
                className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm disabled:opacity-50"
              >
                {models.length === 0 ? (
                  <option value={model}>（无可用选项）</option>
                ) : (
                  models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                      {m.recommended ? " ⭐" : ""}
                    </option>
                  ))
                )}
              </select>
            )}
            {refreshHint && (
              <p className="text-[10px] mt-1 text-slate-500">{refreshHint}</p>
            )}
          </label>

          {(isCustom || current?.base_url != null) && (
            <label className="block">
              <span className="text-xs text-slate-400">
                Base URL{isCustom ? "" : "（可覆盖默认端点，如百炼 Token Plan 专属地址）"}
              </span>
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={current?.base_url ?? "https://..."}
                aria-label="视觉端点 Base URL"
                className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
              />
              {isCustom && (
                <p className="text-[10px] text-slate-600 mt-1">
                  HuggingFace Endpoints 需以 <code className="text-slate-400">/v1</code> 结尾——
                  只填到域名时会自动补上。
                </p>
              )}
            </label>
          )}

          <label className="block">
            <span className="text-xs text-slate-400">
              API Key{isCustom ? "（HuggingFace 令牌 hf_…）" : "（留空则复用该供应商已保存的密钥）"}
            </span>
            <div className="flex gap-2 mt-1">
              <input
                type={showKey ? "text" : "password"}
                value={apiKeyDraft}
                onChange={(e) => setApiKeyDraft(e.target.value)}
                placeholder={vision?.key_set ? "已配置 — 输入新密钥以覆盖" : "hf_... / sk-..."}
                aria-label="视觉模型 API Key"
                className="flex-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
              />
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                className="text-xs border border-edge text-slate-400 rounded px-2.5 hover:text-accent hover:border-accent/40"
              >
                {showKey ? "隐藏" : "显示"}
              </button>
            </div>
          </label>
        </>
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

      {!result && vision && !vision.configured && vision.hint && (
        <p className="text-[11px] text-amber-300/90">{vision.hint}</p>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => void onProbe()}
          disabled={probing || !vision?.configured}
          title={vision?.configured ? "发送一张测试图，验证模型是否真的能读图" : vision?.hint}
          className="text-sm border border-accent2/40 text-accent2 rounded px-3 py-1.5 hover:bg-accent2/10 disabled:opacity-40"
        >
          {probing ? "测试中…" : "测试视觉"}
        </button>
        <button
          type="button"
          onClick={() => void onSave()}
          disabled={saving}
          className="text-sm border border-edge text-slate-300 rounded px-3 py-1.5 hover:border-accent/40 hover:text-accent disabled:opacity-40"
        >
          {saving ? "保存中…" : "保存视觉设置"}
        </button>
      </div>
    </div>
  );
}
