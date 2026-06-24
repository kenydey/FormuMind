import { useStore } from "../store";
import SimPlaceholder from "./SimPlaceholder";

function RmseTrend({ history, metric }: { history: Record<string, number>[]; metric: string }) {
  const series = history.map((snap) => snap[metric]).filter((v) => v != null);
  if (series.length < 1) return null;
  const last = series[series.length - 1];
  const first = series[0];
  const improving = series.length > 1 && last < first;
  return (
    <span className={`font-mono text-[11px] ${improving ? "text-emerald-400" : "text-slate-300"}`}>
      {last.toFixed(last < 1 ? 3 : 1)}
      {improving && " ↓"}
    </span>
  );
}

export default function LoopModal() {
  const { runLoop, busy, loopReport, rmseHistory, doePlan, optimizeEngine, loopDoeEngine, setOptimizeEngine, setLoopDoeEngine } = useStore();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-slate-400 max-w-md">
          一键自驱动闭环：读取已录入实验数据 → 用最新混合模型寻优 → 自动生成下一批主动学习
          DOE。形成 实验→数据→优化→新实验 的数字闭环。
        </p>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <select
            value={optimizeEngine}
            onChange={(e) => setOptimizeEngine(e.target.value as "auto" | "baybe" | "legacy")}
            className="bg-ink border border-edge rounded px-2 py-1 text-[10px]"
            title="闭环寻优引擎"
          >
            <option value="auto">寻优：自动</option>
            <option value="legacy">寻优：经典链</option>
            <option value="baybe">寻优：baybe</option>
          </select>
          <select
            value={loopDoeEngine}
            onChange={(e) => setLoopDoeEngine(e.target.value as "auto" | "legacy" | "baybe")}
            className="bg-ink border border-edge rounded px-2 py-1 text-[10px]"
            title="下一批 DOE 引擎"
          >
            <option value="auto">DOE：自动</option>
            <option value="legacy">DOE：经典 EI</option>
            <option value="baybe">DOE：baybe</option>
          </select>
          <button
            disabled={busy !== "idle"}
            onClick={runLoop}
            className="border border-accent2 text-accent2 hover:bg-accent2/10 rounded px-3 py-1.5 text-xs disabled:opacity-40"
          >
            {busy === "looping" ? "迭代中…" : "🔄 迭代一轮闭环"}
          </button>
        </div>
      </div>

      {loopReport && (
        <div className="space-y-4">
          {/* Loop status row */}
          <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
            <span>引擎：<span className="font-mono text-accent2">{loopReport.engine}</span></span>
            <span>已录入实验：<span className="font-mono text-accent2">{loopReport.total_records}</span> 条</span>
            <span>本域模型：<span className="font-mono text-accent2">{loopReport.model_info.length}</span> 个</span>
          </div>

          {/* Model RMSE/R² cards */}
          {loopReport.model_info.length > 0 ? (
            <div className="grid grid-cols-2 gap-2">
              {loopReport.model_info.map((m) => (
                <div key={`${m.domain}-${m.metric}`} className="border border-edge/40 rounded p-2 bg-ink/40 text-[11px]">
                  <div className="text-accent2 font-mono truncate">{m.metric}</div>
                  <div className="text-slate-500">{m.backend} · n={m.n_samples}</div>
                  <div className="flex justify-between text-slate-400">
                    <span>R²={m.r2.toFixed(2)}</span>
                    <span>RMSE=<RmseTrend history={rmseHistory} metric={m.metric} /></span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-slate-500">
              暂无训练模型（实验样本不足）。当前使用经验先验 + LHS 选点；录入 ≥4 条实验后模型自动激活。
            </p>
          )}

          {/* Convergence chart (reuses optimizationHistory) */}
          <div className="h-48 [&>div]:h-full">
            <SimPlaceholder />
          </div>

          {/* Next DOE batch */}
          {doePlan && (
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <h4 className="text-xs uppercase tracking-widest text-slate-400">
                  下一批推荐实验 · Next DOE（紫色 = AI 主动选点）
                </h4>
                <button
                  onClick={() => useStore.getState().exportDoe("csv")}
                  className="text-[10px] border border-edge text-slate-400 rounded px-1.5 py-0.5 hover:text-accent hover:border-accent/50"
                >
                  导出下一批 DOE
                </button>
              </div>
              <div className="max-h-48 overflow-y-auto border border-edge rounded">
                <table className="w-full text-[11px]">
                  <thead className="sticky top-0 bg-panel">
                    <tr className="text-slate-400">
                      <th className="text-left px-2 py-1">#</th>
                      {doePlan.factors.map((f) => (
                        <th key={f.name} className="text-right px-2 py-1 font-normal">
                          {f.name.replace(" (DGEBA)", "").slice(0, 10)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {doePlan.runs.map((run) => (
                      <tr
                        key={run.run_id}
                        className={`border-t border-edge/40 ${run.ai_suggested ? "border-l-2 border-l-violet-500/70 bg-violet-500/5" : ""}`}
                      >
                        <td className="px-2 py-1 text-slate-500">
                          {run.run_id}
                          {run.ai_suggested && <span className="ml-1 text-[9px] text-violet-400 font-mono">AI</span>}
                        </td>
                        {doePlan.factors.map((f) => (
                          <td key={f.name} className="text-right px-2 py-1 font-mono text-slate-300">
                            {run.natural[f.name]}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {!loopReport && busy !== "looping" && (
        <p className="text-slate-500 text-sm">
          点击「迭代一轮闭环」运行：寻优结果将刷新配方排行，下一批 DOE 将自动生成并标记最有信息量的实验点。
        </p>
      )}
    </div>
  );
}
