import { useState } from "react";
import type { Formulation } from "../api";

interface PatentRisk {
  patent_id: string;
  title: string;
  risk: "high" | "medium" | "low" | "unknown";
  claim_overlap: string;
  recommendation: string;
}

interface MoleculePatentCheck {
  name: string;
  smiles: string;
  patented: boolean | null;
}

interface IPReport {
  formulation_name: string;
  novelty_score: number;
  risks: PatentRisk[];
  whitespace_hints: string[];
  raw_patents_searched: number;
  engine: string;
  molecule_checks?: MoleculePatentCheck[];
}

const RISK_COLOR: Record<string, string> = {
  high: "text-red-400 border-red-500/40 bg-red-500/10",
  medium: "text-amber-400 border-amber-500/40 bg-amber-500/10",
  low: "text-emerald-400 border-emerald-500/40 bg-emerald-500/10",
  unknown: "text-slate-400 border-slate-500/40 bg-slate-500/10",
};

function NoveltyGauge({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(1, score));
  const color = pct > 0.7 ? "#34d399" : pct > 0.4 ? "#fbbf24" : "#f87171";
  const angle = pct * 180;
  const r = 28;
  const cx = 36, cy = 36;
  const toXY = (deg: number) => {
    const rad = ((deg - 180) * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  };
  const start = toXY(0);
  const end = toXY(angle);
  const large = angle > 180 ? 1 : 0;
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width="72" height="44" viewBox="0 0 72 44">
        <path d={`M ${toXY(0).x} ${toXY(0).y} A ${r} ${r} 0 0 1 ${toXY(180).x} ${toXY(180).y}`}
          fill="none" stroke="#1e293b" strokeWidth="6" strokeLinecap="round" />
        {pct > 0.01 && (
          <path d={`M ${start.x} ${start.y} A ${r} ${r} 0 ${large} 1 ${end.x} ${end.y}`}
            fill="none" stroke={color} strokeWidth="6" strokeLinecap="round" />
        )}
        <text x={cx} y={cy + 6} textAnchor="middle" fill={color} fontSize="11" fontFamily="monospace">
          {(score * 100).toFixed(0)}%
        </text>
      </svg>
      <span className="text-[10px] text-slate-400">新颖性评分</span>
    </div>
  );
}

export default function IPReportModal({ form }: { form: Formulation }) {
  const [report, setReport] = useState<IPReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runAnalysis = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/ip/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ formulation: form, limit_patents: 8 }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setReport(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-400">
          配方：<span className="text-slate-200">{form.name}</span>
        </p>
        <button
          onClick={runAnalysis}
          disabled={loading}
          className="text-xs border border-accent2 text-accent2 hover:bg-accent2/10 rounded px-3 py-1.5 disabled:opacity-40"
        >
          {loading ? "分析中…" : "运行 IP 分析"}
        </button>
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      {report && (
        <div className="space-y-4">
          <div className="flex items-center gap-6 p-3 bg-edge/40 rounded-lg">
            <NoveltyGauge score={report.novelty_score} />
            <div className="text-xs space-y-1">
              <p className="text-slate-300">
                引擎：<span className="font-mono text-accent2">{report.engine}</span>
              </p>
              <p className="text-slate-300">
                检索专利：<span className="font-mono text-accent2">{report.raw_patents_searched}</span> 件
              </p>
              <p className={`${report.novelty_score > 0.7 ? "text-emerald-400" : report.novelty_score > 0.4 ? "text-amber-400" : "text-red-400"}`}>
                {report.novelty_score > 0.7 ? "✓ 新颖性较高" : report.novelty_score > 0.4 ? "⚠ 存在部分重叠" : "✕ 存在较高侵权风险"}
              </p>
            </div>
          </div>

          {report.risks.length > 0 && (
            <div>
              <h4 className="text-xs uppercase tracking-widest text-slate-400 mb-2">风险专利</h4>
              <div className="space-y-2">
                {report.risks.map((r) => (
                  <div key={r.patent_id} className={`border rounded p-2.5 text-xs ${RISK_COLOR[r.risk] ?? RISK_COLOR.unknown}`}>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-mono text-[10px]">{r.patent_id}</span>
                      <span className="uppercase text-[10px] font-semibold tracking-wide">{r.risk}</span>
                    </div>
                    <p className="text-slate-200 mb-1">{r.title}</p>
                    <p className="text-slate-400">{r.claim_overlap}</p>
                    <p className="mt-1 italic">{r.recommendation}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {(report.molecule_checks?.length ?? 0) > 0 && (
            <div>
              <h4 className="text-xs uppercase tracking-widest text-slate-400 mb-2">
                分子级专利预筛（molbloom · SureChEMBL）
              </h4>
              <div className="space-y-1">
                {report.molecule_checks!.map((c, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between gap-2 text-xs border border-edge/60 rounded px-2 py-1.5"
                  >
                    <span className="text-slate-300 truncate" title={c.smiles}>
                      {c.name}
                    </span>
                    {c.patented === true ? (
                      <span className="text-amber-400 shrink-0">🔒 已见于专利</span>
                    ) : c.patented === false ? (
                      <span className="text-emerald-400 shrink-0">✓ 未见收录</span>
                    ) : (
                      <span className="text-slate-500 shrink-0">— 未知</span>
                    )}
                  </div>
                ))}
              </div>
              <p className="text-[10px] text-slate-500 mt-1">
                布隆过滤器预筛仅判断分子是否出现在专利语料，不代表权利要求覆盖；正式结论需 FTO 检索。
              </p>
            </div>
          )}

          {report.whitespace_hints.length > 0 && (
            <div>
              <h4 className="text-xs uppercase tracking-widest text-slate-400 mb-2">技术空白区提示</h4>
              <ul className="space-y-1">
                {report.whitespace_hints.map((h, i) => (
                  <li key={i} className="text-xs text-emerald-400 flex gap-2">
                    <span className="shrink-0">→</span>
                    <span>{h}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {!report && !loading && (
        <p className="text-slate-500 text-sm">点击"运行 IP 分析"检索相关专利并评估配方新颖性。</p>
      )}
    </div>
  );
}
