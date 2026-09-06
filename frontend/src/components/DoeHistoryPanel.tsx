import { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "../api";
import { useStore } from "../store";

export type DoeHistoryItem = {
  plan_id?: string;
  design?: string;
  domain?: string;
  notes?: string;
  campaign_id?: number | null;
  round?: number | null;
  created_at?: string | null;
  runs?: unknown[];
  factors?: unknown[];
};

/**
 * Paginated DOE plan history (GET /api/doe/history), scoped to the active
 * workbench campaign when available.
 */
export default function DoeHistoryPanel() {
  const workbenchCampaignId = useStore((s) => s.workbenchCampaignId);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<DoeHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<"campaign" | "all">("campaign");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listDoeHistory({
        campaignId: scope === "campaign" ? workbenchCampaignId : null,
        page: 1,
        pageSize: 20,
      });
      setItems((res.items || []) as DoeHistoryItem[]);
      setTotal(res.total ?? 0);
    } catch (e) {
      setItems([]);
      setTotal(0);
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [scope, workbenchCampaignId]);

  useEffect(() => {
    if (!open) return;
    void load();
  }, [open, load]);

  return (
    <div className="mt-3 border border-edge/50 rounded-lg" data-testid="doe-history-panel">
      <button
        type="button"
        data-testid="doe-history-toggle"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 px-2.5 py-2 text-[11px] text-slate-400 hover:text-slate-200"
      >
        <span>DOE 历史记录{total > 0 && open ? ` · ${total}` : ""}</span>
        <span className="font-mono text-slate-600">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="border-t border-edge/40 px-2.5 py-2 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex gap-1 text-[10px]">
              <button
                type="button"
                onClick={() => setScope("campaign")}
                disabled={workbenchCampaignId == null}
                className={`px-2 py-0.5 rounded border ${
                  scope === "campaign"
                    ? "border-accent text-accent bg-accent/10"
                    : "border-edge text-slate-500"
                }`}
                title={workbenchCampaignId != null ? "当前台账" : "请先创建实验台账"}
              >
                当前台账
              </button>
              <button
                type="button"
                onClick={() => setScope("all")}
                className={`px-2 py-0.5 rounded border ${
                  scope === "all"
                    ? "border-accent text-accent bg-accent/10"
                    : "border-edge text-slate-500"
                }`}
              >
                全部
              </button>
            </div>
            <button
              type="button"
              onClick={() => void load()}
              className="text-[10px] text-slate-500 hover:text-accent"
              disabled={loading}
            >
              刷新
            </button>
          </div>
          {error && <p className="text-[11px] text-rose-300">{error}</p>}
          {loading && <p className="text-[11px] text-slate-500">加载中…</p>}
          {!loading && !error && items.length === 0 && (
            <p className="text-[11px] text-slate-500">暂无 DOE 历史。</p>
          )}
          {!loading && items.length > 0 && (
            <ul className="max-h-40 overflow-y-auto space-y-1.5" data-testid="doe-history-list">
              {items.map((it) => (
                <li
                  key={String(it.plan_id ?? `${it.design}-${it.created_at}`)}
                  className="rounded border border-edge/40 bg-ink/40 px-2 py-1.5 text-[11px]"
                >
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-slate-300">
                    <span className="font-mono text-accent2">{it.design || "—"}</span>
                    {it.round != null && <span className="text-slate-500">R{it.round}</span>}
                    <span className="text-slate-500">
                      {(Array.isArray(it.runs) ? it.runs.length : 0)} runs
                    </span>
                    {it.created_at && (
                      <span className="text-slate-600 font-mono text-[10px]">
                        {it.created_at.slice(0, 19).replace("T", " ")}
                      </span>
                    )}
                  </div>
                  {it.notes && (
                    <div className="text-slate-500 truncate mt-0.5" title={it.notes}>
                      {it.notes}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
