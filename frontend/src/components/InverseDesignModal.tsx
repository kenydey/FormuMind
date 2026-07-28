import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../store";
import {
  api,
  awaitTaskStream,
  formatApiError,
  type DesignCandidate,
  type HardConstraint,
  type InverseDesignResult,
  type ObjectiveSpec,
} from "../api";

/**
 * Target properties in, formulations out.
 *
 * The distinction the UI has to make legible is hard constraint vs soft
 * objective: a VOC ceiling is a pass/fail gate, whereas "maximise salt spray"
 * is something to trade against cost. Everywhere else in the app both are
 * ObjectiveSpec weights, which cannot express "must not exceed".
 */

const METRIC_LABELS: Record<string, string> = {
  salt_spray_hours: "耐盐雾 (h)",
  cost_cny_per_kg: "成本 (CNY/kg)",
  voc_gpl: "VOC (g/L)",
  cleaning_efficiency: "清洗率 (%)",
  coating_weight_gsm: "膜重 (g/m²)",
  film_weight_gsm: "干膜重 (g/m²)",
  adhesion_mpa: "附着力 (MPa)",
};

const label = (metric: string) => METRIC_LABELS[metric] ?? metric.replace(/_/g, " ");

const OP_LABELS: Record<HardConstraint["op"], string> = {
  ge: "≥ 不低于",
  le: "≤ 不超过",
  between: "介于",
};

function defaultConstraints(domain: string): HardConstraint[] {
  if (domain === "degreaser") {
    return [{ metric: "cleaning_efficiency", op: "ge", value: 90 }];
  }
  return [
    { metric: "salt_spray_hours", op: "ge", value: 1000 },
    { metric: "voc_gpl", op: "le", value: 250 },
    { metric: "cost_cny_per_kg", op: "le", value: 40 },
  ];
}

function defaultObjectives(domain: string): ObjectiveSpec[] {
  const primary =
    domain === "degreaser" ? "cleaning_efficiency" : "salt_spray_hours";
  return [
    { metric: primary, weight: 0.5, direction: "maximize" } as ObjectiveSpec,
    { metric: "cost_cny_per_kg", weight: 0.5, direction: "minimize" } as ObjectiveSpec,
  ];
}

export default function InverseDesignModal() {
  const { requirement, setLeaderboard } = useStore(
    useShallow((s) => ({ requirement: s.requirement, setLeaderboard: s.setLeaderboard }))
  );

  const [hard, setHard] = useState<HardConstraint[]>(() =>
    defaultConstraints(requirement.domain)
  );
  const [population, setPopulation] = useState(48);
  const [generations, setGenerations] = useState(30);
  const [seedWithLlm, setSeedWithLlm] = useState(true);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<InverseDesignResult | null>(null);

  function updateConstraint(index: number, patch: Partial<HardConstraint>) {
    setHard((prev) => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  }

  async function run() {
    setBusy(true);
    setError("");
    setResult(null);
    setProgress("提交中…");
    try {
      const accepted = await api.startInverseDesign(
        requirement,
        { hard, soft: defaultObjectives(requirement.domain) },
        { population, generations, seed_with_llm: seedWithLlm }
      );
      const final = await awaitTaskStream(accepted.task_id, (ev) =>
        setProgress(ev.message || "搜索中…")
      );
      const data = final.data as (InverseDesignResult & { error?: string }) | undefined;
      if (!data || data.error) throw new Error(data?.error || "搜索失败");
      setResult(data);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
      setProgress("");
    }
  }

  function adopt(candidates: DesignCandidate[]) {
    setLeaderboard(candidates.map((c) => c.formulation));
  }

  const frontier = (result?.candidates ?? []).filter((c) => c.pareto_rank === 0);
  const distinctSets = new Set(
    (result?.candidates ?? []).map((c) => [...c.materials].sort().join("|"))
  ).size;

  return (
    <div className="space-y-4 text-sm">
      <p className="text-slate-400">
        给定目标性能，反向搜索配方。硬约束必须满足，软目标之间做权衡，返回帕累托前沿上
        <span className="text-accent">成分组成互不相同</span>的候选。
      </p>

      <div className="space-y-2">
        <div className="text-slate-300 font-medium">硬约束 · 必须满足</div>
        {hard.map((c, i) => (
          <div key={`${c.metric}-${i}`} className="flex items-center gap-2">
            <span className="w-36 text-slate-400">{label(c.metric)}</span>
            <select
              className="bg-panel border border-edge rounded px-2 py-1"
              value={c.op}
              onChange={(e) =>
                updateConstraint(i, { op: e.target.value as HardConstraint["op"] })
              }
            >
              {Object.entries(OP_LABELS).map(([op, text]) => (
                <option key={op} value={op}>
                  {text}
                </option>
              ))}
            </select>
            <input
              type="number"
              className="w-28 bg-panel border border-edge rounded px-2 py-1"
              value={c.value}
              onChange={(e) => updateConstraint(i, { value: Number(e.target.value) })}
            />
            {c.op === "between" && (
              <input
                type="number"
                className="w-28 bg-panel border border-edge rounded px-2 py-1"
                value={c.value_max ?? c.value}
                onChange={(e) => updateConstraint(i, { value_max: Number(e.target.value) })}
              />
            )}
            <button
              className="text-slate-500 hover:text-red-400 px-1"
              onClick={() => setHard((prev) => prev.filter((_, k) => k !== i))}
              title="移除该约束"
            >
              ×
            </button>
          </div>
        ))}
        <button
          className="text-xs text-accent hover:underline"
          onClick={() =>
            setHard((prev) => [...prev, { metric: "cost_cny_per_kg", op: "le", value: 50 }])
          }
        >
          + 添加约束
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-4 text-xs text-slate-400">
        <label className="flex items-center gap-1">
          种群
          <input
            type="number"
            className="w-20 bg-panel border border-edge rounded px-2 py-1"
            value={population}
            min={8}
            max={400}
            onChange={(e) => setPopulation(Number(e.target.value))}
          />
        </label>
        <label className="flex items-center gap-1">
          迭代代数
          <input
            type="number"
            className="w-20 bg-panel border border-edge rounded px-2 py-1"
            value={generations}
            min={1}
            max={300}
            onChange={(e) => setGenerations(Number(e.target.value))}
          />
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={seedWithLlm}
            onChange={(e) => setSeedWithLlm(e.target.checked)}
          />
          用 LLM 播种初始种群
        </label>
      </div>

      <button
        className="px-4 py-2 rounded bg-accent/20 border border-accent/40 text-accent
                   hover:bg-accent/30 disabled:opacity-50"
        onClick={run}
        disabled={busy || hard.length === 0}
      >
        {busy ? progress || "搜索中…" : "🎯 开始逆向设计"}
      </button>

      {error && (
        <div className="text-red-400 bg-red-400/10 border border-red-400/20 rounded p-2">
          {error}
        </div>
      )}

      {result && (
        <div className="space-y-3">
          <div className="text-xs text-slate-400">
            引擎 {result.engine} · {result.generations} 代 · {result.evaluations} 次评估 ·
            淘汰 {result.rejected_infeasible} 个不可行 ·{" "}
            <span className="text-accent">{distinctSets} 种不同成分组成</span>
          </div>
          {result.warnings.map((w) => (
            <div
              key={w}
              className="text-xs text-yellow-400/80 bg-yellow-400/10 border border-yellow-400/20 rounded p-2"
            >
              {w}
            </div>
          ))}

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-slate-400">
                <tr>
                  <th className="text-left py-1">层级</th>
                  <th className="text-left">状态</th>
                  <th className="text-left">成分组成</th>
                  <th className="text-right">主指标</th>
                  <th className="text-right">成本</th>
                  <th className="text-right">VOC</th>
                </tr>
              </thead>
              <tbody>
                {result.candidates.slice(0, 12).map((c, i) => {
                  const p = c.formulation.predicted ?? {};
                  const primary =
                    p.salt_spray_hours ?? p.cleaning_efficiency ?? p.coating_weight_gsm;
                  return (
                    <tr key={i} className="border-t border-edge/50">
                      <td className="py-1">
                        <span
                          className={
                            c.pareto_rank === 0 ? "text-accent font-medium" : "text-slate-500"
                          }
                        >
                          {c.pareto_rank === 0 ? "前沿" : `rank ${c.pareto_rank}`}
                        </span>
                      </td>
                      <td className={c.feasible ? "text-green-400" : "text-red-400"}>
                        {c.feasible ? "满足" : `违反 ${c.violation.toFixed(2)}`}
                      </td>
                      <td className="text-slate-300 max-w-[22rem] truncate" title={c.materials.join("、")}>
                        {c.materials.join("、")}
                      </td>
                      <td className="text-right">{primary != null ? primary.toFixed(0) : "—"}</td>
                      <td className="text-right">
                        {p.cost_cny_per_kg != null ? p.cost_cny_per_kg.toFixed(1) : "—"}
                      </td>
                      <td className="text-right">
                        {p.voc_gpl != null ? p.voc_gpl.toFixed(0) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <button
            className="px-3 py-1.5 rounded bg-panel border border-edge text-slate-300
                       hover:border-accent/50 disabled:opacity-50"
            onClick={() => adopt(frontier.length ? frontier : result.candidates.slice(0, 5))}
            disabled={result.candidates.length === 0}
          >
            采用前沿候选到配方排行
          </button>
        </div>
      )}
    </div>
  );
}
