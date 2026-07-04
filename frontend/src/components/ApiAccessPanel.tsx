import { useEffect, useState } from "react";
import { api, formatApiError, getApiToken, setApiToken } from "../api";

export default function ApiAccessPanel({
  onTokenSaved,
}: {
  onTokenSaved?: () => void;
}) {
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);
  const [tokenDraft, setTokenDraft] = useState(getApiToken() ?? "");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api
      .getAuthStatus()
      .then((s) => setAuthRequired(s.auth_required))
      .catch(() => setAuthRequired(null));
  }, []);

  if (authRequired !== true) return null;

  const hasToken = !!getApiToken();

  return (
    <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-3 space-y-2">
      <div className="text-xs text-amber-200 font-semibold">API 访问令牌 · Bearer Token</div>
      <p className="text-[11px] text-amber-100/80 leading-relaxed">
        服务器已启用 API 鉴权。请在下方输入与后端{" "}
        <code className="text-amber-200/90">FORMUMIND_API_TOKEN</code> 相同的令牌，否则设置、依赖列表等接口无法加载。
      </p>
      <div className="flex gap-2">
        <input
          type="password"
          value={tokenDraft}
          onChange={(e) => {
            setTokenDraft(e.target.value);
            setSaved(false);
          }}
          placeholder="粘贴 API Token"
          className="flex-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
        />
        <button
          type="button"
          onClick={() => {
            if (!tokenDraft.trim()) return;
            setApiToken(tokenDraft.trim());
            setSaved(true);
            onTokenSaved?.();
          }}
          className="text-xs bg-accent/90 text-ink font-semibold rounded px-3 py-1.5 shrink-0"
        >
          保存令牌
        </button>
      </div>
      {hasToken && !saved && (
        <p className="text-[10px] text-emerald-400/90">已保存令牌 — 若仍无法加载，请确认与服务器 .env 一致后点「刷新」</p>
      )}
      {saved && (
        <p className="text-[10px] text-emerald-400">令牌已更新，正在重新加载…</p>
      )}
    </div>
  );
}

export function isAuthError(err: unknown): boolean {
  const msg = formatApiError(err).toLowerCase();
  return msg.includes("401") || msg.includes("api token") || msg.includes("missing api token");
}
