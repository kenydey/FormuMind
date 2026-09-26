import type { FormulationExplain } from "../api";

/** Batch C: expand-able 「为何推荐」 block for FormulaLeaderboard cards. */
export default function FormulaExplainPanel({
  explain,
  score,
}: {
  explain?: FormulationExplain | null;
  score?: number | null;
}) {
  if (!explain && score == null) return null;

  const hits = explain?.objectives_hit ?? [];
  const misses = explain?.constraints_miss ?? [];
  const refs = explain?.evidence_refs ?? [];
  const kg = explain?.kg_signals;
  const supply = explain?.supply_flags ?? [];
  const uncertainty = explain?.uncertainty ?? [];
  const notes = explain?.notes ?? [];
  const effectTrace = explain?.effect_trace ?? [];

  return (
    <details
      className="rounded border border-edge/50 bg-ink/30 px-2 py-1.5"
      data-testid="formula-explain-panel"
      open={Boolean(misses.length || supply.length || explain?.bias_corrected || effectTrace.some((t) => t.status === "unwired"))}
    >
      <summary className="cursor-pointer text-[11px] text-slate-300 select-none list-none flex items-center gap-2">
        <span className="text-accent2">为何推荐</span>
        {score != null && (
          <span className="font-mono text-accent text-[10px]">score {score.toFixed(2)}</span>
        )}
        {explain?.bias_corrected && (
          <span
            className="text-[9px] px-1 rounded border border-violet-500/40 text-violet-300"
            data-testid="formula-explain-bias"
          >
            已偏差校准
          </span>
        )}
        <span className="text-[9px] text-slate-500 ml-auto">展开</span>
      </summary>
      <div className="mt-2 space-y-1.5 text-[10px] text-slate-400">
        {hits.length > 0 && (
          <Section title="目标命中" tone="emerald" items={hits} testId="explain-hits" />
        )}
        {misses.length > 0 && (
          <Section title="约束未满足" tone="amber" items={misses} testId="explain-misses" />
        )}
        {effectTrace.length > 0 && (
          <div data-testid="explain-effect-trace">
            <div className="text-slate-500 mb-0.5">约束追踪</div>
            <div className="flex flex-wrap gap-1">
              {effectTrace.slice(0, 12).map((t) => (
                <span
                  key={t.field}
                  title={t.detail || t.consumers?.join(",") || undefined}
                  className={`text-[9px] px-1.5 py-0.5 rounded border ${
                    t.status === "wired"
                      ? "border-emerald-500/30 text-emerald-300 bg-emerald-500/10"
                      : t.status === "unwired"
                        ? "border-rose-500/30 text-rose-300 bg-rose-500/10"
                        : "border-slate-500/30 text-slate-400 bg-slate-500/10"
                  }`}
                  data-testid={`effect-trace-${t.status}`}
                >
                  {t.label}
                  {t.status === "wired" ? " · 生效" : t.status === "unwired" ? " · 未接线" : " · 仅展示"}
                </span>
              ))}
            </div>
          </div>
        )}
        {kg && (
          <div data-testid="explain-kg">
            <div className="text-slate-500 mb-0.5">KG 信号</div>
            <div className="flex flex-wrap gap-1">
              <Chip ok={kg.feasible !== false}>{kg.feasible === false ? "不相容" : "相容"}</Chip>
              {(kg.measured_materials || []).slice(0, 4).map((m) => (
                <Chip key={m} ok>
                  实测 {m}
                </Chip>
              ))}
              {(kg.synergizes || []).slice(0, 3).map((s) => (
                <Chip key={s} ok>
                  协同 {s}
                </Chip>
              ))}
              {(kg.inhibits || []).slice(0, 3).map((s) => (
                <Chip key={s} ok={false}>
                  抑制 {s}
                </Chip>
              ))}
            </div>
          </div>
        )}
        {refs.length > 0 && (
          <div data-testid="explain-evidence">
            <div className="text-slate-500 mb-0.5">证据</div>
            <div className="flex flex-wrap gap-1">
              {refs.slice(0, 8).map((r) => (
                <span
                  key={`${r.source_type}:${r.source_id}`}
                  className="font-mono text-[9px] px-1 py-0.5 rounded bg-edge/50 text-slate-300"
                >
                  {r.source_type}:{r.source_id.slice(0, 24)}
                </span>
              ))}
            </div>
          </div>
        )}
        {supply.length > 0 && (
          <Section title="供应风险" tone="amber" items={supply} testId="explain-supply" />
        )}
        {uncertainty.length > 0 && (
          <Section title="不确定性" tone="slate" items={uncertainty} testId="explain-uncertainty" />
        )}
        {notes.length > 0 && (
          <Section title="备注" tone="slate" items={notes.slice(0, 4)} testId="explain-notes" />
        )}
        {!explain && (
          <p className="text-slate-500" data-testid="explain-score-only">
            仅有评分、尚无结构化解释（旧结果或未走推荐打分路径）。
          </p>
        )}
      </div>
    </details>
  );
}

function Section({
  title,
  items,
  tone,
  testId,
}: {
  title: string;
  items: string[];
  tone: "emerald" | "amber" | "slate";
  testId: string;
}) {
  const color =
    tone === "emerald" ? "text-emerald-300" : tone === "amber" ? "text-amber-300" : "text-slate-300";
  return (
    <div data-testid={testId}>
      <div className="text-slate-500 mb-0.5">{title}</div>
      <ul className={`list-disc pl-4 space-y-0.5 ${color}`}>
        {items.map((it) => (
          <li key={it}>{it}</li>
        ))}
      </ul>
    </div>
  );
}

function Chip({ children, ok }: { children: React.ReactNode; ok: boolean }) {
  return (
    <span
      className={`text-[9px] px-1.5 py-0.5 rounded border ${
        ok
          ? "border-emerald-500/30 text-emerald-300 bg-emerald-500/10"
          : "border-amber-500/30 text-amber-300 bg-amber-500/10"
      }`}
    >
      {children}
    </span>
  );
}
