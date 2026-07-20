import { useState } from "react";
import {
  LineChart,
  Line,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts";
import type { ModelInfo, FactorCandidate } from "../api";
import { api, primaryObjectiveMetric } from "../api";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../store";
import { AdaptiveDoeInsights } from "./AdaptiveDoeInsights";

function R2Gauge({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value));
  const angle = pct * 180;
  const r = 20;
  const cx = 28, cy = 28;
  const toXY = (deg: number) => {
    const rad = ((deg - 180) * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  };
  const start = toXY(0);
  const end = toXY(angle);
  const large = angle > 180 ? 1 : 0;
  const color = pct > 0.85 ? "#34d399" : pct > 0.6 ? "#fbbf24" : "#f87171";
  return (
    <svg width="56" height="32" viewBox="0 0 56 36" className="shrink-0">
      <path d={`M ${toXY(0).x} ${toXY(0).y} A ${r} ${r} 0 0 1 ${toXY(180).x} ${toXY(180).y}`}
        fill="none" stroke="#1e293b" strokeWidth="5" strokeLinecap="round" />
      {pct > 0.01 && (
        <path d={`M ${start.x} ${start.y} A ${r} ${r} 0 ${large} 1 ${end.x} ${end.y}`}
          fill="none" stroke={color} strokeWidth="5" strokeLinecap="round" />
      )}
      <text x={cx} y={cy + 8} textAnchor="middle" fill={color} fontSize="9" fontFamily="monospace">
        {(value * 100).toFixed(0)}%
      </text>
    </svg>
  );
}

function R2Trend({ trend }: { trend: number[] }) {
  if (trend.length < 2) return null;
  const data = trend.map((r2, i) => ({ t: i + 1, r2 }));
  const color = trend[trend.length - 1] > 0.85
    ? "#34d399"
    : trend[trend.length - 1] > 0.6
      ? "#fbbf24"
      : "#f87171";
  return (
    <div className="mt-1.5 h-10">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <ReferenceLine y={0.85} stroke="#34d399" strokeDasharray="3 2" strokeOpacity={0.35} />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 4, fontSize: 10, padding: "2px 6px" }}
            labelFormatter={(v) => `训练 #${v}`}
            formatter={(v: number) => [`R²=${v.toFixed(3)}`, ""]}
            itemStyle={{ color }}
          />
          <Line
            type="monotone"
            dataKey="r2"
            stroke={color}
            strokeWidth={1.5}
            dot={{ r: 2, fill: color }}
            activeDot={{ r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ModelCard({ m, trend }: { m: ModelInfo; trend: number[] }) {
  return (
    <div className="border border-edge/40 rounded p-1.5 bg-ink/40">
      <div className="flex items-center gap-2">
        <R2Gauge value={m.r2} />
        <div className="text-[10px] leading-snug min-w-0">
          <div className="text-accent2 font-mono truncate">{m.metric}</div>
          <div className="text-slate-500">{m.backend} · n={m.n_samples}</div>
          <div className="text-slate-500">
            RMSE={m.rmse.toFixed(m.rmse < 1 ? 3 : 1)}
            {m.cv_r2 != null ? ` · cvR²=${m.cv_r2.toFixed(2)}` : ""}
          </div>
        </div>
      </div>
      <R2Trend trend={trend} />
    </div>
  );
}

const NATIVE_DESIGNS = [
  { value: "full_factorial", label: "全因子" },
  { value: "fractional_factorial", label: "部分因子" },
  { value: "plackett_burman", label: "Plackett-Burman" },
  { value: "ccd", label: "中心复合 CCD" },
  { value: "lhs", label: "拉丁超立方" },
];

const PYDOE_DESIGNS = [
  { value: "bbdesign", label: "Box-Behnken (pyDOE)" },
  { value: "simplex_lattice", label: "混合物 simplex (pyDOE)" },
  { value: "sobol", label: "Sobol 序列 (pyDOE)" },
];

const AI_DESIGN = { value: "ai_active", label: "🧠 AI 主动选点" };

export default function DoeResultsPanel() {
  const [factorHints, setFactorHints] = useState<FactorCandidate[] | null>(null);
  const [factorBusy, setFactorBusy] = useState(false);
  const {
    requirement, doePlan, models, modelHistory, trainMessage,
    busy, generateDoe, exportDoe, importCsv, error,
    doeEngine, alEngine, setDoeEngine, setAlEngine, lastAlEngine, campaignState,
    workbenchCampaignId, workbenchStats, workbenchAdoptedPlanId, optimizationHistory, setOpenModal,
    runNextRoundDoe, adoptDoePlanToWorkbench, adaptiveDoe,
  } = useStore(
    useShallow((s) => ({
      requirement: s.requirement,
      doePlan: s.doePlan,
      adaptiveDoe: s.adaptiveDoe,
      models: s.models,
      modelHistory: s.modelHistory,
      trainMessage: s.trainMessage,
      busy: s.busy,
      generateDoe: s.generateDoe,
      exportDoe: s.exportDoe,
      importCsv: s.importCsv,
      error: s.error,
      doeEngine: s.doeEngine,
      alEngine: s.alEngine,
      setDoeEngine: s.setDoeEngine,
      setAlEngine: s.setAlEngine,
      lastAlEngine: s.lastAlEngine,
      campaignState: s.campaignState,
      workbenchCampaignId: s.workbenchCampaignId,
      workbenchStats: s.workbenchStats,
      workbenchAdoptedPlanId: s.workbenchAdoptedPlanId,
      optimizationHistory: s.optimizationHistory,
      setOpenModal: s.setOpenModal,
      runNextRoundDoe: s.runNextRoundDoe,
      adoptDoePlanToWorkbench: s.adoptDoePlanToWorkbench,
    }))
  );
  const metric = primaryObjectiveMetric(requirement);
  const pendingAdopt =
    !!doePlan && (!doePlan.plan_id || doePlan.plan_id !== workbenchAdoptedPlanId);

  const designs =
    doeEngine === "pydoe"
      ? [...NATIVE_DESIGNS.filter((d) => ["ccd", "lhs"].includes(d.value)), ...PYDOE_DESIGNS, AI_DESIGN]
      : doeEngine === "native"
        ? [...NATIVE_DESIGNS, AI_DESIGN]
        : [...NATIVE_DESIGNS, ...PYDOE_DESIGNS, AI_DESIGN];

  function trendFor(m: ModelInfo): number[] {
    return modelHistory
      .flatMap((snapshot) =>
        snapshot.filter((s) => s.domain === m.domain && s.metric === m.metric).map((s) => s.r2)
      );
  }

  async function loadFactorHints() {
    setFactorBusy(true);
    try {
      const res = await api.suggestFactors(requirement);
      setFactorHints(res.factors);
    } catch {
      setFactorHints([]);
    } finally {
      setFactorBusy(false);
    }
  }

  const optChartData = optimizationHistory.map((v, i) => ({ iter: i + 1, score: v }));

  return (
    <section className="glass rounded-xl p-4 overflow-y-auto">
      {error && (
        <div className="mb-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h2 className="text-sm uppercase tracking-widest text-accent2">DOE 实验设计</h2>
        <div className="flex flex-wrap gap-2 items-center">
          <select
            value={doeEngine}
            onChange={(e) => setDoeEngine(e.target.value as "auto" | "native" | "pydoe")}
            className="bg-ink border border-edge rounded px-2 py-1 text-xs"
            title="冷启动 DOE 引擎"
          >
            <option value="auto">DOE 引擎：自动</option>
            <option value="native">经典 native</option>
            <option value="pydoe">增强 pydoe</option>
          </select>
          <select
            value={alEngine}
            onChange={(e) => setAlEngine(e.target.value as "auto" | "legacy" | "baybe")}
            className="bg-ink border border-edge rounded px-2 py-1 text-xs"
            title="主动学习引擎（AI 选点时生效）"
          >
            <option value="auto">AL 引擎：自动</option>
            <option value="legacy">经典 EI</option>
            <option value="baybe">baybe Campaign</option>
          </select>
          <select
            id="doe-design"
            defaultValue="ccd"
            className="bg-ink border border-edge rounded px-2 py-1 text-xs"
          >
            {designs.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
          <button
            disabled={busy !== "idle"}
            onClick={() => generateDoe((document.getElementById("doe-design") as HTMLSelectElement).value)}
            className="text-xs border border-accent text-accent rounded px-2 py-1 hover:bg-accent/10 disabled:opacity-40"
          >
            {busy === "doe" ? "生成中…" : "生成 DOE"}
          </button>
          <button
            type="button"
            disabled={factorBusy}
            onClick={() => void loadFactorHints()}
            className="text-xs border border-teal-500/50 text-teal-300 rounded px-2 py-1 hover:bg-teal-500/10 disabled:opacity-40"
          >
            {factorBusy ? "分析中…" : "AI 建议因子"}
          </button>
          <label className="text-xs border border-edge text-slate-400 rounded px-2 py-1 hover:text-accent hover:border-accent/50 cursor-pointer">
            导入 CSV
            <input
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              disabled={busy !== "idle"}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) importCsv(f);
                e.target.value = "";
              }}
            />
          </label>
        </div>
      </div>

      {factorHints && factorHints.length > 0 && (
        <div className="mb-3 rounded border border-teal-500/30 bg-teal-500/5 p-2 text-[11px] text-slate-300">
          <div className="text-teal-300/90 mb-1 font-medium">KB + 需求 levers 因子建议</div>
          <ul className="space-y-1 max-h-32 overflow-y-auto">
            {factorHints.map((f) => (
              <li key={f.name}>
                <span className="font-mono text-accent2">{f.name}</span>{" "}
                [{f.low}–{f.high} {f.unit}] — {f.rationale.slice(0, 120)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {optChartData.length > 1 && (
        <div className="mb-3 h-24 rounded border border-edge/40 bg-ink/40 p-2">
          <div className="text-[10px] text-slate-500 mb-1">优化收敛曲线（最佳得分）</div>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={optChartData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <XAxis dataKey="iter" hide />
              <YAxis hide domain={["auto", "auto"]} />
              <Tooltip
                contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 10 }}
                formatter={(v: number) => [v.toFixed(3), metric]}
              />
              <Line type="monotone" dataKey="score" stroke="#34d399" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {pendingAdopt && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded border border-accent/30 bg-accent/5 px-2.5 py-2">
          <span className="text-[11px] text-slate-300">
            闭环已生成下一批 DOE，尚未写入实验台账
          </span>
          <button
            type="button"
            disabled={busy !== "idle"}
            onClick={() => {
              void adoptDoePlanToWorkbench().then((id) => {
                if (id != null) setOpenModal("workbench");
              });
            }}
            className="text-[10px] border border-accent text-accent rounded px-2 py-1 hover:bg-accent/10 disabled:opacity-40"
          >
            {busy === "doe" ? "创建中…" : "创建实验台账 →"}
          </button>
        </div>
      )}

      {workbenchStats && workbenchStats.completed > 0 && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <span className="text-[11px] text-slate-400">
            实验台账进度：{workbenchStats.completed}/{workbenchStats.total} 已完成（{workbenchStats.strategy}）
          </span>
          <button
            type="button"
            disabled={busy !== "idle"}
            onClick={() => {
              if (pendingAdopt) {
                void adoptDoePlanToWorkbench().then((id) => {
                  if (id != null) setOpenModal("workbench");
                });
              } else {
                void runNextRoundDoe();
              }
            }}
            className="text-[10px] border border-violet-500/50 text-violet-300 rounded px-2 py-1 hover:bg-violet-500/10 disabled:opacity-40"
          >
            {busy === "doe"
              ? "处理中…"
              : pendingAdopt
                ? "采用下一批 DOE →"
                : "下一轮 AI 实验 →"}
          </button>
        </div>
      )}

      {models.length > 0 && (
        <div className="mb-3 grid grid-cols-2 gap-1.5">
          {models.map((m) => (
            <ModelCard key={`${m.domain}-${m.metric}`} m={m} trend={trendFor(m)} />
          ))}
        </div>
      )}
      {trainMessage && <div className="text-[11px] text-slate-500 mb-2">{trainMessage}</div>}

      {!doePlan ? (
        <p className="text-slate-500 text-sm">
          生成 DOE 设计后，请在 <span className="text-accent">实验台账</span> 中填报实测{" "}
          <span className="text-accent font-mono">{metric}</span>，再回灌训练数据驱动模型。
        </p>
      ) : (
        <>
          <div className="flex items-center justify-between gap-2 mb-2">
            <span className="text-[11px] text-slate-500 min-w-0 truncate">
              {doePlan.notes}
              {lastAlEngine && (
                <span className="ml-2 text-violet-400 font-mono">AL={lastAlEngine}</span>
              )}
              {campaignState && (
                <span className="ml-2 text-slate-600" title="baybe Campaign 状态已保存到会话">· campaign ✓</span>
              )}
            </span>
            <div className="flex gap-1.5 shrink-0">
              <button
                onClick={() => exportDoe("csv")}
                className="text-[10px] border border-edge text-slate-400 rounded px-1.5 py-0.5 hover:text-accent hover:border-accent/50"
                title="导出 DOE 实验记录表（含空白实测列）"
              >
                导出 CSV
              </button>
              <button
                onClick={() => exportDoe("xlsx")}
                className="text-[10px] border border-edge text-slate-400 rounded px-1.5 py-0.5 hover:text-accent hover:border-accent/50"
                title="导出 XLSX（需后端 openpyxl）"
              >
                XLSX
              </button>
            </div>
          </div>

          <div className="border border-accent/30 bg-accent/5 rounded-lg px-3 py-2.5 mb-3 space-y-2">
            <p className="text-[11px] text-slate-300">
              {workbenchCampaignId != null
                ? "台账已创建。请在实验台账中填写实际参数与实测值，保存后回灌训练。"
                : "生成成功。打开实验台账填报实测数据。"}
            </p>
            <button
              type="button"
              onClick={() => setOpenModal("workbench")}
              className="text-xs border border-accent2 text-accent2 rounded px-3 py-1.5 hover:bg-accent2/10"
            >
              打开实验台账 →
            </button>
          </div>

          <AdaptiveDoeInsights meta={adaptiveDoe} doePlan={doePlan} />

          <div className="max-h-40 overflow-y-auto border border-edge rounded">
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
                      {run.ai_suggested && (
                        <span className="ml-1 text-[9px] text-violet-400 font-mono">AI</span>
                      )}
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
        </>
      )}
    </section>
  );
}
