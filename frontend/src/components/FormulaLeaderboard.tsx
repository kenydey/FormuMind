import { useState } from "react";
import { useStore } from "../store";
import type { Formulation } from "../api";
import { copyFormulaJson, downloadFormulaCsv, exportFormulaToPdf } from "../utils/export";
import MolViewer from "./MolViewer";

function ExportMenu({ form }: { form: Formulation }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    await copyFormulaJson(form);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
    setOpen(false);
  };

  return (
    <div className="relative">
      <button
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        className="text-[10px] border border-edge text-slate-400 rounded px-1.5 py-0.5 hover:text-accent hover:border-accent/50"
        title="导出配方"
      >
        {copied ? "已复制 ✓" : "导出 ▾"}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={(e) => { e.stopPropagation(); setOpen(false); }} />
          <div className="absolute right-0 mt-1 z-20 w-28 bg-panel border border-edge rounded shadow-lg text-[11px] overflow-hidden">
            {[
              { label: "复制 JSON", fn: onCopy },
              { label: "导出 CSV", fn: () => { downloadFormulaCsv(form); setOpen(false); } },
              { label: "导出 PDF", fn: () => { exportFormulaToPdf(form); setOpen(false); } },
            ].map((item) => (
              <button
                key={item.label}
                onClick={(e) => { e.stopPropagation(); item.fn(); }}
                className="block w-full text-left px-2.5 py-1.5 text-slate-300 hover:bg-accent/10 hover:text-accent"
              >
                {item.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function FormulaCard({ form, rank }: { form: Formulation; rank: number }) {
  const [open, setOpen] = useState(rank === 1);
  return (
    <div className="border border-edge rounded-lg p-3 bg-ink/60">
      <div className="w-full flex items-center justify-between gap-2">
        <button className="flex items-center gap-2 min-w-0 flex-1" onClick={() => setOpen((o) => !o)}>
          <span className="text-accent2 font-mono text-xs shrink-0">#{rank}</span>
          <span className="text-sm text-slate-200 truncate">{form.name}</span>
        </button>
        <div className="flex items-center gap-2 shrink-0">
          {form.score != null && (
            <span className="text-accent font-mono text-sm">{form.score.toFixed(2)}</span>
          )}
          <ExportMenu form={form} />
        </div>
      </div>
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
          <MolViewer entries={form.ingredients.map((i) => ({ name: i.name, smiles: i.smiles }))} />
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
