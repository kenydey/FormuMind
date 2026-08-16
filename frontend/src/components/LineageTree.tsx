import { useEffect, useState } from "react";
import { api, formatApiError, type WorkbenchRow } from "../api";

interface LineageTreeProps {
  campaignId: number;
  rowId: number;
  onClose: () => void;
}

function rowFactors(r: WorkbenchRow): Record<string, number | string> {
  return { ...(r.planned_params || {}), ...(r.actual_params || {}) };
}

/**
 * Formula lineage chain (Phase 2.3).
 *
 * Walks the parent_sample_id chain and renders it root → current, so a chemist
 * can see at a glance where a formulation came from and what changed each hop.
 */
export default function LineageTree({ campaignId, rowId, onClose }: LineageTreeProps) {
  const [chain, setChain] = useState<WorkbenchRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .getRowLineage(campaignId, rowId)
      .then((rows) => setChain([...rows].reverse())) // backend returns current → root
      .catch((e) => setError(formatApiError(e)))
      .finally(() => setLoading(false));
  }, [campaignId, rowId]);

  const factorKeys = Array.from(
    new Set(chain.flatMap((r) => Object.keys(rowFactors(r))))
  ).slice(0, 8);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-edge rounded-lg shadow-xl w-[34rem] max-w-[92vw] p-4 text-sm"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-slate-200">
            配方谱系（行 #{rowId}）
          </h3>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>

        {loading ? (
          <p className="text-xs text-slate-500">加载谱系…</p>
        ) : error ? (
          <div className="text-red-400 bg-red-400/10 border border-red-400/20 rounded p-2 text-xs">
            {error}
          </div>
        ) : chain.length === 0 ? (
          <p className="text-xs text-slate-500">无谱系信息</p>
        ) : (
          <div className="space-y-0">
            {chain.map((r, i) => {
              const factors = rowFactors(r);
              const isCurrent = i === chain.length - 1;
              return (
                <div key={`${r.campaign_id}:${r.id}`}>
                  {i > 0 && (
                    <div className="text-center text-slate-500 text-[10px] py-0.5">▲ 迭代自</div>
                  )}
                  <div
                    className={`border rounded px-3 py-2 ${
                      isCurrent
                        ? "border-accent/50 bg-accent/5"
                        : "border-edge/60 bg-ink/20"
                    }`}
                  >
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-slate-300 font-medium">
                        第 {chain.length - i} 代 · 行 #{r.id}
                        {r.campaign_id !== campaignId && ` · C${r.campaign_id}`}
                      </span>
                      <span className="text-slate-500">{r.status}</span>
                    </div>
                    {factorKeys.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mt-1.5">
                        {factorKeys.map((k) => {
                          const v = factors[k];
                          if (v === undefined) return null;
                          return (
                            <span
                              key={k}
                              className="text-[10px] px-1.5 py-0.5 rounded bg-panel border border-edge/50 text-slate-300"
                            >
                              {k.slice(0, 10)}: {typeof v === "number" ? v.toFixed(2) : v}
                            </span>
                          );
                        })}
                      </div>
                    )}
                    {Object.keys(r.measurements || {}).length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mt-1">
                        {Object.entries(r.measurements).map(([k, v]) => (
                          <span
                            key={k}
                            className="text-[10px] px-1.5 py-0.5 rounded bg-accent2/10 text-accent2 border border-accent2/20"
                          >
                            {k.slice(0, 12)}: {String(v)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
