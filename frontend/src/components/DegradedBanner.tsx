import { useEffect, useState } from "react";
import { api, formatApiError, getApiToken } from "../api";
import { useStore } from "../store";

/**
 * Surfaces a prominent, actionable banner when the platform is running in
 * offline / degraded mode — i.e. no LLM key configured, or the online-retrieval
 * libraries are not installed. The offline fallbacks still work; this just makes
 * "online mode" a clearly reachable default rather than a silent surprise.
 */
export default function DegradedBanner() {
  const openSettings = useStore((s) => s.openSettings);
  const [dismissed, setDismissed] = useState(false);
  const [reasons, setReasons] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const issues: string[] = [];
      try {
        const auth = await api.getAuthStatus();
        if (auth.auth_required && !getApiToken()) {
          issues.push("未配置 API 访问令牌（设置页顶部可填写）");
        }
      } catch {
        /* auth status unavailable */
      }
      try {
        const s = await api.getSettings();
        if (!s.key_set) issues.push("未配置大模型 API Key");
      } catch (e) {
        if (formatApiError(e).includes("401")) {
          issues.push("API 令牌无效或未配置");
        }
      }
      try {
        const status = await api.getSourceStatus();
        const offline = ["literature", "internet"].filter(
          (k) => status[k] && !status[k].available
        );
        if (offline.length > 0) issues.push("在线检索依赖未安装");
      } catch {
        /* status unavailable — skip */
      }
      if (!cancelled) setReasons(issues);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (dismissed || reasons.length === 0) return null;

  const needsAuth = reasons.some((r) => r.includes("令牌"));
  const needsDeps = reasons.some((r) => r.includes("依赖"));

  return (
    <div className="shrink-0 px-5 py-2 bg-amber-500/10 border-b border-amber-500/30 flex items-center gap-3 text-xs text-amber-300">
      <span className="font-semibold">⚠ 离线降级模式</span>
      <span className="text-amber-200/80">
        {reasons.join(" · ")} —— 当前部分功能（深度研究、在线检索、跨源交叉验证）以离线兜底运行。
      </span>
      <button
        onClick={() => openSettings(needsDeps ? "deps" : needsAuth ? "api" : "llm")}
        className="ml-auto shrink-0 border border-amber-400/40 text-amber-200 rounded px-2.5 py-1 hover:bg-amber-400/15"
      >
        {needsAuth ? "去配置令牌" : needsDeps ? "去安装依赖" : "去配置大模型"} →
      </button>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 text-amber-200/60 hover:text-amber-100"
        title="忽略"
      >
        ✕
      </button>
    </div>
  );
}
