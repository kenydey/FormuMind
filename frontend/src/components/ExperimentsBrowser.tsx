import { useEffect, useState } from "react";
import { api, formatApiError, type ExperimentSummary } from "../api";
import { useStore } from "../store";

/**
 * Browse stored experiments (training corpus / ELN-backed rows).
 * Opened from the Actions panel as modal "experiments".
 */
export default function ExperimentsBrowser() {
  const activeProjectId = useStore((s) => s.activeProjectId);
  const domain = useStore((s) => s.requirement.domain);
  const [rows, setRows] = useState<ExperimentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<"project" | "all">("project");
  const [selected, setSelected] = useState<ExperimentSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const list = await api.listExperiments({
          domain,
          project_id: scope === "project" && activeProjectId ? activeProjectId : undefined,
          limit: 100,
        });
        if (!cancelled) {
          setRows(list);
          setSelected(null);
        }
      } catch (e) {
        if (!cancelled) {
          setRows([]);
          setError(formatApiError(e));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeProjectId, domain, scope]);

  return (
    <div className="space-y-3" data-testid="experiments-browser">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] text-slate-500">
          浏览已入库实验记录（可用于 QC 回看与训练语料审计）。
        </p>
        <div className="flex gap-1 text-[10px]">
          <button
            type="button"
            onClick={() => setScope("project")}
            className={`px-2 py-0.5 rounded border ${
              scope === "project"
                ? "border-accent text-accent bg-accent/10"
                : "border-edge text-slate-500"
            }`}
            disabled={!activeProjectId}
            title={activeProjectId ? "仅当前项目" : "请先打开项目"}
          >
            当前项目
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
      </div>

      {error && (
        <div className="text-xs text-rose-400 border border-rose-500/30 bg-rose-500/10 rounded px-2 py-1.5">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-slate-500 py-6 text-center">加载实验列表…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-slate-500 py-6 text-center">暂无实验记录</p>
      ) : (
        <div className="max-h-80 overflow-y-auto border border-edge/50 rounded-lg divide-y divide-edge/40">
          {rows.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => setSelected(r)}
              className={`w-full text-left px-3 py-2 hover:bg-accent/5 transition-colors ${
                selected?.id === r.id ? "bg-accent/10" : ""
              }`}
            >
              <div className="flex items-center gap-2 text-xs">
                <span className="font-mono text-accent2">#{r.id}</span>
                <span className="text-slate-200 truncate">{r.label || "（无标签）"}</span>
                <span className="ml-auto text-[10px] text-slate-500">{r.source || "—"}</span>
              </div>
              <div className="mt-0.5 text-[10px] text-slate-500 flex gap-2">
                <span>{r.domain}</span>
                <span>·</span>
                <span>{r.measurement_count} 测量</span>
                {r.created_at && (
                  <>
                    <span>·</span>
                    <span className="font-mono">{r.created_at.slice(0, 16).replace("T", " ")}</span>
                  </>
                )}
              </div>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div className="border border-edge rounded-lg p-3 bg-ink/40 space-y-2">
          <h4 className="text-xs uppercase tracking-widest text-accent2">
            实验 #{selected.id} · 实测摘要
          </h4>
          {Object.keys(selected.measured || {}).length === 0 ? (
            <p className="text-[11px] text-slate-500">无 measured 快照（可能仅有 typed measurements）</p>
          ) : (
            <div className="grid grid-cols-2 gap-1.5">
              {Object.entries(selected.measured).map(([k, v]) => (
                <div
                  key={k}
                  className="flex justify-between text-[11px] border border-edge/40 rounded px-2 py-1"
                >
                  <span className="text-slate-400 truncate">{k}</span>
                  <span className="font-mono text-slate-200">{v}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
