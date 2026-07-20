import type { AdaptiveDOEMetadata, DOEPlan, RunExplanation } from "../api";

const STRATEGY_LABELS: Record<string, string> = {
  exploration: "探索为主",
  balanced: "探索 + 利用",
  exploitation: "利用为主",
};

function explanationForRun(explanations: RunExplanation[], runId: number): RunExplanation | undefined {
  return explanations.find((e) => e.run_id === runId);
}

export function AdaptiveDoeInsights({
  meta,
  doePlan,
  compact = false,
}: {
  meta: AdaptiveDOEMetadata | null | undefined;
  doePlan?: DOEPlan | null;
  compact?: boolean;
}) {
  if (!meta) return null;

  const hasContent =
    meta.strategy_rationale ||
    meta.recommended_next_action ||
    meta.run_explanations.length > 0 ||
    meta.anomalies.length > 0;
  if (!hasContent) return null;

  return (
    <div className={`rounded-lg border border-violet-500/30 bg-violet-500/5 ${compact ? "p-2 space-y-2" : "p-3 space-y-3"}`}>
      {(meta.strategy_label || meta.strategy_rationale) && (
        <div className="text-[11px] text-slate-300">
          <span className="text-violet-300 font-medium">
            策略：{STRATEGY_LABELS[meta.strategy_label] ?? meta.strategy_label}
          </span>
          {meta.strategy_rationale && (
            <p className="mt-1 text-slate-400 leading-relaxed">{meta.strategy_rationale}</p>
          )}
        </div>
      )}

      {meta.recommended_next_action && (
        <div className="text-[11px] rounded border border-teal-500/30 bg-teal-500/5 px-2 py-1.5 text-teal-200">
          下一步：{meta.recommended_next_action}
        </div>
      )}

      {meta.anomalies.length > 0 && (
        <div className="text-[11px]">
          <div className="text-amber-300/90 font-medium mb-1">异常实验点 ({meta.anomalies.length})</div>
          <ul className="space-y-1 max-h-24 overflow-y-auto text-slate-400">
            {meta.anomalies.slice(0, 5).map((a) => (
              <li key={`${a.experiment_id}-${a.type}`}>
                <span className="font-mono text-amber-200/80">{a.experiment_id}</span> — {a.note}
              </li>
            ))}
          </ul>
        </div>
      )}

      {doePlan && meta.run_explanations.length > 0 && (
        <div className="text-[11px]">
          <div className="text-violet-300/90 font-medium mb-1">推荐解释</div>
          <ul className="space-y-1.5 max-h-32 overflow-y-auto">
            {doePlan.runs
              .filter((r) => r.ai_suggested)
              .map((run) => {
                const expl = explanationForRun(meta.run_explanations, run.run_id);
                if (!expl) return null;
                return (
                  <li key={run.run_id} className="text-slate-400 leading-relaxed">
                    <span className="font-mono text-violet-300">#{run.run_id}</span> {expl.summary}
                    {expl.constraint_warnings && expl.constraint_warnings.length > 0 && (
                      <span className="block text-amber-300/80 mt-0.5">⚠ {expl.constraint_warnings[0]}</span>
                    )}
                  </li>
                );
              })}
          </ul>
        </div>
      )}
    </div>
  );
}
