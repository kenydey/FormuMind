import { useEffect, useState } from "react";
import { api } from "../api";

interface RoundItem {
  round: number;
  loop_entry?: {
    converged?: boolean;
    engine?: string;
    rmse_by_metric?: Record<string, number>;
    loop_message?: string;
  } | null;
  doe_plan?: {
    plan_id?: string;
    design?: string;
    runs?: unknown[];
  } | null;
  ledger_rows?: { item_id?: string }[];
}

export default function CampaignRoundsModal({
  campaignId,
  onClose,
}: {
  campaignId: number;
  onClose: () => void;
}) {
  const [rounds, setRounds] = useState<RoundItem[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [unassociated, setUnassociated] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const pageSize = 5;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.listCampaignRounds(campaignId, { page, pageSize });
        if (!cancelled) {
          setRounds((data.rounds ?? []) as unknown as RoundItem[]);
          setTotal(data.total_rounds ?? 0);
          setUnassociated(data.unassociated_ledger ?? 0);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [campaignId, page]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-panel border border-edge rounded-lg shadow-xl w-[720px] max-w-[92vw] max-h-[82vh] flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-edge/40">
          <h3 className="text-sm font-semibold">轮次历史（DOE + 台账）</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {loading && <p className="text-xs text-slate-500">加载中…</p>}
          {error && <p className="text-xs text-red-400">{error}</p>}
          {!loading && !error && rounds.length === 0 && (
            <p className="text-xs text-slate-500">暂无轮次记录（尚未触发闭环优化）</p>
          )}

          {rounds.map((r) => (
            <div key={r.round} className="border border-edge/40 rounded p-3 bg-ink/20">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-accent2">Round {r.round}</span>
                <span className="text-[10px] text-slate-500">
                  {r.loop_entry?.converged ? "已收敛" : ""}
                  {r.loop_entry?.engine ? ` · ${r.loop_entry.engine}` : ""}
                </span>
              </div>

              {r.doe_plan ? (
                <div className="mt-2 text-[11px] text-slate-400">
                  DOE：{r.doe_plan.design ?? "?"}（{r.doe_plan.runs?.length ?? 0} 个实验点）
                  {r.doe_plan.plan_id && (
                    <a
                      className="ml-2 text-accent2 hover:underline"
                      href={api.doeExportUrl(r.doe_plan.plan_id)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      导出
                    </a>
                  )}
                </div>
              ) : (
                <div className="mt-2 text-[11px] text-slate-600">无 DOE 记录</div>
              )}

              <div className="mt-1.5 text-[11px] text-slate-400">
                台账 {r.ledger_rows?.length ?? 0} 行
                {r.loop_entry?.rmse_by_metric &&
                Object.keys(r.loop_entry.rmse_by_metric).length > 0 ? (
                  <span className="ml-2">
                    RMSE: {JSON.stringify(r.loop_entry.rmse_by_metric)}
                  </span>
                ) : null}
                {r.loop_entry?.loop_message ? (
                  <div className="mt-1 text-[10px] text-slate-500">
                    {r.loop_entry.loop_message}
                  </div>
                ) : null}
              </div>
            </div>
          ))}

          {unassociated > 0 && (
            <p className="text-[10px] text-slate-500">
              另有 {unassociated} 条台账未关联到轮次
            </p>
          )}
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-2 border-t border-edge/40">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
              className="text-xs border border-edge rounded px-2 py-1 disabled:opacity-40 text-slate-400"
            >
              上一页
            </button>
            <span className="text-xs text-slate-500">
              {page} / {totalPages}
            </span>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
              className="text-xs border border-edge rounded px-2 py-1 disabled:opacity-40 text-slate-400"
            >
              下一页
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
