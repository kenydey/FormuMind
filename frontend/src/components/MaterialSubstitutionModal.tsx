import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../store";
import {
  api,
  formatApiError,
  type SubstituteCandidate,
  type SubstitutionReport,
  type SupplyRiskReport,
} from "../api";

/**
 * What could replace this component, and what would it cost.
 *
 * The ranking signal users actually need is the predicted property delta, not
 * a similarity score — so the table leads with "what changes" and annotates
 * how much resolution that prediction has. Without RDKit the predictor can
 * only separate same-role materials on cost and VOC, and presenting an
 * unchanged salt-spray figure as a finding would mislead.
 */

const CONFIDENCE_NOTE: Record<SubstituteCandidate["delta_confidence"], string> = {
  high: "分子描述符可用，性能预测有分辨力",
  low: "跨角色替换，预测仅供参考",
  cost_only: "未装 RDKit：同角色换料仅成本/VOC 有分辨力，性能指标不可信",
};

const CONFIDENCE_STYLE: Record<SubstituteCandidate["delta_confidence"], string> = {
  high: "text-green-400",
  low: "text-yellow-400",
  cost_only: "text-yellow-400",
};

const SHOWN_METRICS = ["cost_cny_per_kg", "voc_gpl", "salt_spray_hours", "cleaning_efficiency"];

const METRIC_LABELS: Record<string, string> = {
  cost_cny_per_kg: "成本",
  voc_gpl: "VOC",
  salt_spray_hours: "耐盐雾",
  cleaning_efficiency: "清洗率",
};

function DeltaCell({ pct }: { pct: number | null | undefined }) {
  if (pct == null) return <span className="text-slate-600">—</span>;
  if (Math.abs(pct) < 0.05) return <span className="text-slate-500">持平</span>;
  return (
    <span className={pct > 0 ? "text-green-400" : "text-red-400"}>
      {pct > 0 ? "+" : ""}
      {pct.toFixed(1)}%
    </span>
  );
}

export default function MaterialSubstitutionModal() {
  const { requirement, leaderboard } = useStore(
    useShallow((s) => ({ requirement: s.requirement, leaderboard: s.leaderboard }))
  );

  const formulation = leaderboard[0];
  const ingredients = formulation?.ingredients ?? [];
  const [material, setMaterial] = useState("");
  const [report, setReport] = useState<SubstitutionReport | null>(null);
  const [risk, setRisk] = useState<SupplyRiskReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.supplyRisk().then(setRisk).catch(() => setRisk(null));
  }, []);

  useEffect(() => {
    if (!material && ingredients.length) setMaterial(ingredients[0].name);
  }, [ingredients, material]);

  async function run() {
    if (!material) return;
    setBusy(true);
    setError("");
    setReport(null);
    try {
      setReport(
        await api.findSubstitutes({
          requirement,
          formulation: formulation ?? undefined,
          material,
          limit: 10,
        })
      );
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  const atRisk = Object.entries(risk?.at_risk ?? {});

  return (
    <div className="space-y-4 text-sm">
      <p className="text-slate-400">
        选一个成分，查看可替代的材料及其
        <span className="text-accent">预测性能偏离</span>。化学上不相容的替代品会排在最后并附拦截原因。
      </p>

      {atRisk.length > 0 && (
        <div className="text-xs bg-yellow-400/10 border border-yellow-400/20 rounded p-2">
          <div className="text-yellow-400">⚠ 供应风险</div>
          <div className="text-slate-400 mt-0.5">
            {atRisk.map(([name, status]) => `${name}（${status}）`).join("、")}
            {risk?.affected.length ? ` · 影响 ${risk.affected.length} 个配方` : ""}
          </div>
        </div>
      )}

      {ingredients.length === 0 ? (
        <div className="text-slate-500">
          配方排行为空——先运行推荐或逆向设计，再回到这里查看替代方案。
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <select
            className="bg-panel border border-edge rounded px-2 py-1 flex-1"
            value={material}
            onChange={(e) => setMaterial(e.target.value)}
          >
            {ingredients.map((ing) => (
              <option key={ing.name} value={ing.name}>
                {ing.name}（{ing.role} · {ing.weight_pct}%）
              </option>
            ))}
          </select>
          <button
            className="px-3 py-1.5 rounded bg-accent/20 border border-accent/40 text-accent
                       hover:bg-accent/30 disabled:opacity-50"
            onClick={run}
            disabled={busy || !material}
          >
            {busy ? "分析中…" : "🔍 查找替代"}
          </button>
        </div>
      )}

      {error && (
        <div className="text-red-400 bg-red-400/10 border border-red-400/20 rounded p-2">
          {error}
        </div>
      )}

      {report && (
        <div className="space-y-2">
          <div className="text-xs text-slate-400">
            替换 <span className="text-slate-200">{report.original}</span>
            {report.substitute_group && ` · 可互换组 ${report.substitute_group}`} · 共考察{" "}
            {report.total_considered} 个候选
          </div>

          {report.candidates.length === 0 ? (
            <div className="text-slate-500 text-xs">
              目录中没有同组或同角色的替代品。可在「材料空间」新增材料，或从文献产品登记簿提升。
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-slate-400">
                  <tr>
                    <th className="text-left py-1">候选材料</th>
                    <th className="text-right">相似度</th>
                    {SHOWN_METRICS.map((m) => (
                      <th key={m} className="text-right">
                        {METRIC_LABELS[m]}
                      </th>
                    ))}
                    <th className="text-left pl-2">可行性</th>
                  </tr>
                </thead>
                <tbody>
                  {report.candidates.map((c) => (
                    <tr key={c.material} className="border-t border-edge/50 align-top">
                      <td className="py-1">
                        <div className="text-slate-200">{c.material}</div>
                        <div className="text-slate-500">
                          {c.functional_class}
                          {c.availability !== "in_stock" && (
                            <span className="text-yellow-400"> · {c.availability}</span>
                          )}
                        </div>
                      </td>
                      <td className="text-right">{(c.structural_score * 100).toFixed(0)}%</td>
                      {SHOWN_METRICS.map((m) => (
                        <td key={m} className="text-right">
                          <DeltaCell pct={c.deltas[m]?.pct} />
                        </td>
                      ))}
                      <td className="pl-2">
                        {c.feasible ? (
                          <span className={CONFIDENCE_STYLE[c.delta_confidence]}>
                            可用
                          </span>
                        ) : (
                          <span
                            className="text-red-400"
                            title={c.blocking_reasons.join("\n")}
                          >
                            拦截
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {report.candidates[0] && (
            <div
              className={`text-xs ${CONFIDENCE_STYLE[report.candidates[0].delta_confidence]}`}
            >
              ⓘ {CONFIDENCE_NOTE[report.candidates[0].delta_confidence]}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
