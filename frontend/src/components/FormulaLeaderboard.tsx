import { useRef, useState } from "react";
import { useStore } from "../store";
import type { Formulation } from "../api";
import {
  copyFormulaJson,
  copyLeaderboardJson,
  downloadFormulaCsv,
  downloadLeaderboardCsv,
  exportFormulaToPdf,
  exportLeaderboardToPdf,
} from "../utils/export";
import MolViewer from "./MolViewer";
import Modal from "./Modal";
import IPReportModal from "./IPReportModal";
import FormulaTableView from "./FormulaTableView";

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

function FormulaCard({
  form,
  rank,
  cardRef,
  forceOpen,
}: {
  form: Formulation;
  rank: number;
  cardRef?: (el: HTMLDivElement | null) => void;
  forceOpen?: boolean;
}) {
  const [open, setOpen] = useState(rank === 1);
  const [ipOpen, setIpOpen] = useState(false);
  const expanded = open || forceOpen;

  // Color swatch from CIELAB values when available (CSS Color Level 4 lab()).
  const labL = form.predicted?.["lab_L"];
  const labA = form.predicted?.["lab_a"];
  const labB = form.predicted?.["lab_b"];
  const deltaE = form.predicted?.["delta_e"];
  const hasColor = labL != null && labA != null && labB != null;
  const swatchBg = hasColor ? `lab(${labL} ${labA} ${labB})` : undefined;

  // PVC summary badge.
  const pvcVal = form.predicted?.["pvc_pct"];
  const cpvcVal = form.predicted?.["cpvc_pct"];
  const ratio = form.predicted?.["pvc_to_cpvc_ratio"];

  return (
    <div ref={cardRef} className="border border-edge rounded-lg p-3 bg-ink/60">
      <div className="w-full flex items-center justify-between gap-2">
        <button className="flex items-center gap-2 min-w-0 flex-1" onClick={() => setOpen((o) => !o)}>
          <span className="text-accent2 font-mono text-xs shrink-0">#{rank}</span>
          {hasColor && (
            <span
              className="shrink-0 w-4 h-4 rounded border border-edge/60"
              style={{ background: swatchBg }}
              title={`L*=${labL?.toFixed(1)} a*=${labA?.toFixed(1)} b*=${labB?.toFixed(1)}${deltaE != null ? `  ΔE₀₀=${deltaE.toFixed(1)}` : ""}`}
            />
          )}
          <span className="text-sm text-slate-200 truncate">{form.name}</span>
        </button>
        <div className="flex items-center gap-2 shrink-0">
          {form.score != null && (
            <span className="text-accent font-mono text-sm">{form.score.toFixed(2)}</span>
          )}
          <ExportMenu form={form} />
        </div>
      </div>
      {expanded && (
        <div className="mt-2 space-y-2">
          {/* PVC / CPVC summary row */}
          {pvcVal != null && (
            <div className="flex flex-wrap gap-1 text-[10px]">
              <span className="bg-edge/60 px-1.5 py-0.5 rounded text-slate-300">
                PVC <span className="font-mono text-accent2">{pvcVal.toFixed(1)}%</span>
              </span>
              {cpvcVal != null && (
                <span className="bg-edge/60 px-1.5 py-0.5 rounded text-slate-300">
                  CPVC <span className="font-mono text-accent2">{cpvcVal.toFixed(1)}%</span>
                </span>
              )}
              {ratio != null && (
                <span
                  className={`bg-edge/60 px-1.5 py-0.5 rounded ${ratio < 1 ? "text-emerald-400" : "text-amber-400"}`}
                  title={ratio < 1 ? "PVC < CPVC: good barrier film" : "PVC > CPVC: porous film, poor barrier"}
                >
                  PVC/CPVC <span className="font-mono">{ratio.toFixed(2)}</span>
                  {ratio < 1 ? " ✓" : " ⚠"}
                </span>
              )}
              {form.predicted?.["solids_by_volume_pct"] != null && (
                <span className="bg-edge/60 px-1.5 py-0.5 rounded text-slate-300">
                  SBV <span className="font-mono text-accent2">{form.predicted["solids_by_volume_pct"].toFixed(1)}%</span>
                </span>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-1">
            {Object.entries(form.predicted)
              .filter(([k]) => !["lab_L","lab_a","lab_b","pvc_pct","cpvc_pct","pvc_to_cpvc_ratio","solids_by_volume_pct"].includes(k))
              .map(([k, v]) => {
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
          <button
            onClick={(e) => { e.stopPropagation(); setIpOpen(true); }}
            className="w-full mt-1 text-[10px] border border-edge text-slate-400 rounded px-2 py-1 hover:text-accent2 hover:border-accent2/50"
          >
            🔍 IP 合规分析
          </button>
        </div>
      )}
      <Modal
        title={`IP 分析 · ${form.name}`}
        open={ipOpen}
        onClose={() => setIpOpen(false)}
        size="lg"
        nested
      >
        <IPReportModal form={form} />
      </Modal>
    </div>
  );
}

function ListExportMenu({ forms }: { forms: Formulation[] }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  if (forms.length === 0) return null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-[11px] border border-edge text-slate-400 rounded px-2 py-1 hover:text-accent hover:border-accent/50"
      >
        {copied ? "已复制 ✓" : "导出列表 ▾"}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 mt-1 z-20 w-32 bg-panel border border-edge rounded shadow-lg text-[11px] overflow-hidden">
            {[
              {
                label: "复制 JSON",
                fn: async () => {
                  await copyLeaderboardJson(forms);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                  setOpen(false);
                },
              },
              { label: "导出 CSV", fn: () => { downloadLeaderboardCsv(forms); setOpen(false); } },
              { label: "导出 PDF", fn: () => { void exportLeaderboardToPdf(forms); setOpen(false); } },
            ].map((item) => (
              <button
                key={item.label}
                type="button"
                onClick={() => void item.fn()}
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

export default function FormulaLeaderboard() {
  const { leaderboard, requirement } = useStore();
  const [viewMode, setViewMode] = useState<"cards" | "table">("cards");
  const [highlightIndex, setHighlightIndex] = useState<number | null>(null);
  const cardRefs = useRef<(HTMLDivElement | null)[]>([]);

  function switchToCard(index: number) {
    setViewMode("cards");
    setHighlightIndex(index);
    requestAnimationFrame(() => {
      cardRefs.current[index]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  return (
    <section className="rounded-xl overflow-y-auto">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <h2 className="text-sm uppercase tracking-widest text-accent2 flex-1 min-w-[140px]">
          Top 推荐配方 · Leaderboard
        </h2>
        <div className="flex items-center gap-1 border border-edge rounded overflow-hidden">
          <button
            type="button"
            onClick={() => setViewMode("cards")}
            className={`text-[11px] px-2.5 py-1 ${viewMode === "cards" ? "bg-accent/20 text-accent" : "text-slate-400"}`}
          >
            卡片
          </button>
          <button
            type="button"
            onClick={() => setViewMode("table")}
            className={`text-[11px] px-2.5 py-1 ${viewMode === "table" ? "bg-accent/20 text-accent" : "text-slate-400"}`}
          >
            列表
          </button>
        </div>
        <ListExportMenu forms={leaderboard} />
      </div>
      {leaderboard.length === 0 ? (
        <p className="text-slate-500 text-sm">尚无配方。先运行检索推荐或寻优。</p>
      ) : viewMode === "table" ? (
        <FormulaTableView
          forms={leaderboard}
          domain={requirement.domain}
          onSelect={switchToCard}
        />
      ) : (
        <div className="space-y-2">
          {leaderboard.map((f, i) => (
            <FormulaCard
              key={`${f.name}-${i}`}
              form={f}
              rank={i + 1}
              cardRef={(el) => {
                cardRefs.current[i] = el;
              }}
              forceOpen={highlightIndex === i}
            />
          ))}
        </div>
      )}
    </section>
  );
}
