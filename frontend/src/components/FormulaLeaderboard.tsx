import { useState } from "react";
import { useStore } from "../store";
import type { Formulation } from "../api";

function FormulaCard({ form, rank }: { form: Formulation; rank: number }) {
  const [open, setOpen] = useState(rank === 1);
  return (
    <div className="border border-edge rounded-lg p-3 bg-ink/60">
      <button className="w-full flex items-center justify-between" onClick={() => setOpen((o) => !o)}>
        <span className="flex items-center gap-2">
          <span className="text-accent2 font-mono text-xs">#{rank}</span>
          <span className="text-sm text-slate-200">{form.name}</span>
        </span>
        {form.score != null && (
          <span className="text-accent font-mono text-sm">{form.score.toFixed(1)}</span>
        )}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          <div className="flex flex-wrap gap-1">
            {Object.entries(form.predicted).map(([k, v]) => {
              const std = form.predicted_std?.[k];
              const stdHigh = std != null && std > Math.abs(v) * 0.2;
              return (
                <span key={k} className="text-[10px] bg-edge px-1.5 py-0.5 rounded text-slate-300">
                  {k}:{" "}
                  <span className={`font-mono ${stdHigh ? "text-amber-400" : "text-accent2"}`}>{v}</span>
                  {std != null && (
                    <span className={`ml-0.5 font-mono ${stdHigh ? "text-amber-500" : "text-slate-500"}`}>
                      ±{std.toFixed(std < 1 ? 3 : 1)}
                    </span>
                  )}
                </span>
              );
            })}
          </div>
          <table className="w-full text-xs">
            <tbody>
              {form.ingredients.map((ing) => (
                <tr key={ing.name} className="border-b border-edge/40">
                  <td className="py-0.5 text-slate-400">{ing.name}</td>
                  <td className="py-0.5 text-right text-slate-200 font-mono">{ing.weight_pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          {form.warnings.length > 0 && (
            <div className="text-[10px] text-amber-400">⚠ {form.warnings.join("; ")}</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function FormulaLeaderboard() {
  const leaderboard = useStore((s) => s.leaderboard);
  return (
    <section className="glass rounded-xl p-4 overflow-y-auto">
      <h2 className="text-sm uppercase tracking-widest text-accent2 mb-3">
        Top 推荐配方 · Leaderboard
      </h2>
      {leaderboard.length === 0 ? (
        <p className="text-slate-500 text-sm">尚无配方。先运行检索推荐或寻优。</p>
      ) : (
        <div className="space-y-2">
          {leaderboard.map((f, i) => (
            <FormulaCard key={`${f.name}-${i}`} form={f} rank={i + 1} />
          ))}
        </div>
      )}
    </section>
  );
}
