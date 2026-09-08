import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../store";
import {
  api,
  formatApiError,
  type ExternalSubstituteCandidate,
  type SubstituteCandidate,
  type SubstitutionReport,
  type SupplyRiskReport,
} from "../api";

/**
 * What could replace this component, and what would it cost.
 *
 * The ranking signal users actually need is the predicted property delta, not
 * a similarity score — so the table leads with "what changes" and annotates
 * how much resolution that prediction has. Launched from the recommended
 * formula's ingredient table (component-level 🔁), so the material is pre-
 * selected; the dropdown remains for pivoting to another component.
 *
 * Networked PubChem similars appear in a second list (default on); they do
 * not carry formula Δ until promoted into the catalog.
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

interface MaterialSubstitutionModalProps {
  initialMaterial?: string;
  onClose: () => void;
}

export default function MaterialSubstitutionModal({
  initialMaterial,
  onClose,
}: MaterialSubstitutionModalProps) {
  const { requirement, leaderboard } = useStore(
    useShallow((s) => ({ requirement: s.requirement, leaderboard: s.leaderboard }))
  );

  const formulation = leaderboard[0];
  const ingredients = formulation?.ingredients ?? [];
  const [material, setMaterial] = useState(initialMaterial ?? "");
  const [report, setReport] = useState<SubstitutionReport | null>(null);
  const [risk, setRisk] = useState<SupplyRiskReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [candidates, setCandidates] = useState<string[]>([]);
  const [includeUnavailable, setIncludeUnavailable] = useState(false);
  const [includeExternal, setIncludeExternal] = useState(true);
  const [promoteMsg, setPromoteMsg] = useState("");
  const [promoting, setPromoting] = useState<string | null>(null);

  useEffect(() => {
    api.supplyRisk().then(setRisk).catch(() => setRisk(null));
  }, []);

  useEffect(() => {
    if (initialMaterial) setMaterial(initialMaterial);
    else if (!material && ingredients.length) setMaterial(ingredients[0].name);
  }, [initialMaterial, ingredients, material]);

  async function run() {
    if (!material) return;
    setBusy(true);
    setError("");
    setCandidates([]);
    setReport(null);
    setPromoteMsg("");
    try {
      setReport(
        await api.findSubstitutes({
          requirement,
          formulation: formulation ?? undefined,
          material,
          limit: 10,
          include_unavailable: includeUnavailable,
          include_external: includeExternal,
          external_limit: 8,
        })
      );
    } catch (err) {
      const fromApi =
        err && typeof err === "object" && "candidates" in err
          ? (err as { candidates?: string[] }).candidates
          : undefined;
      const list = Array.isArray(fromApi) ? fromApi.filter(Boolean) : [];
      setCandidates(list);
      // Prefer the bare message when we render candidates as buttons.
      setError(
        list.length && err instanceof Error
          ? err.message
          : formatApiError(err)
      );
    } finally {
      setBusy(false);
    }
  }

  async function promoteExternal(row: ExternalSubstituteCandidate) {
    const key = row.cid != null ? String(row.cid) : row.name;
    setPromoting(key);
    setPromoteMsg("");
    try {
      const result = await api.proposeMaterial({
        name: row.name,
        cas_no: row.cas_no || undefined,
        smiles: row.smiles || undefined,
        role: row.role_hint || undefined,
        source: "user",
      });
      const action = result.action || "unknown";
      setPromoteMsg(
        action === "upsert" || action === "exists"
          ? `已入库：${result.name || row.name}（${action}）。可再点「查找替代」查看库内 Δ。`
          : action === "pending"
            ? `已进入待入库：${result.name || row.name}`
            : `未入库：${result.name || row.name}（${action}${result.reason ? ` · ${result.reason}` : ""}）`
      );
    } catch (err) {
      setPromoteMsg(formatApiError(err));
    } finally {
      setPromoting(null);
    }
  }

  const atRisk = Object.entries(risk?.at_risk ?? {});
  const external = report?.external ?? [];
  const identity = report?.identity;
  const extMeta = report?.external_meta;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-edge rounded-lg shadow-xl w-[54rem] max-w-[94vw] p-4 text-sm max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
        data-testid="material-substitution-modal"
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-slate-200">材料替代</h3>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>

        <p className="text-slate-400 text-xs mb-3">
          选一个成分，查看库内可替代材料及其
          <span className="text-accent">预测性能偏离</span>
          ；默认同时联网检索结构相似候选（PubChem）。化学上不相容的库内替代会排在最后并附拦截原因。
        </p>

        {atRisk.length > 0 && (
          <div className="text-xs bg-yellow-400/10 border border-yellow-400/20 rounded p-2 mb-3">
            <div className="text-yellow-400">⚠ 供应风险</div>
            <div className="text-slate-400 mt-0.5">
              {atRisk.map(([name, status]) => `${name}（${status}）`).join("、")}
              {risk?.affected.length ? ` · 影响 ${risk.affected.length} 个配方` : ""}
            </div>
          </div>
        )}

        {ingredients.length === 0 ? (
          <div className="text-slate-500">
            配方排行为空——先运行推荐或逆向设计，再查看替代方案。
          </div>
        ) : (
          <div className="flex items-center gap-2 mb-3">
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
              data-testid="run-substitutes"
            >
              {busy ? "分析中…" : "🔍 查找替代"}
            </button>
          </div>
        )}

        <div className="space-y-2 mb-3">
          <label
            className="flex items-start gap-2 text-[11px] text-slate-400 cursor-pointer"
            data-testid="include-unavailable-substitutes"
          >
            <input
              type="checkbox"
              className="mt-0.5"
              checked={includeUnavailable}
              onChange={(e) => setIncludeUnavailable(e.target.checked)}
            />
            <span>
              包含停产材料
              <span className="block text-slate-500">
                默认关闭：替代候选与逆向设计池已排除 discontinued。
              </span>
            </span>
          </label>
          <label
            className="flex items-start gap-2 text-[11px] text-slate-400 cursor-pointer"
            data-testid="include-external-substitutes"
          >
            <input
              type="checkbox"
              className="mt-0.5"
              checked={includeExternal}
              onChange={(e) => setIncludeExternal(e.target.checked)}
            />
            <span>
              联网检索替代（PubChem）
              <span className="block text-slate-500">
                默认开启：按 CAS/SMILES 拉结构相似清单；不算配方 Δ，可一键入库后再算。
              </span>
            </span>
          </label>
        </div>

        {error && (
          <div className="text-red-400 bg-red-400/10 border border-red-400/20 rounded p-2 mb-2">
            <div>{error}</div>
            {candidates.length > 0 && (
              <div className="mt-2 text-slate-300">
                <div className="text-xs text-slate-400 mb-1">配方中的材料（点击选用）：</div>
                <div className="flex flex-wrap gap-1.5">
                  {candidates.map((name) => (
                    <button
                      key={name}
                      type="button"
                      className="px-2 py-0.5 rounded border border-edge text-slate-200
                                 hover:border-accent/50 hover:text-accent"
                      onClick={() => {
                        setMaterial(name);
                        setError("");
                        setCandidates([]);
                      }}
                    >
                      {name}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {promoteMsg && (
          <div className="text-xs text-emerald-300/90 bg-emerald-500/10 border border-emerald-500/20 rounded p-2 mb-2">
            {promoteMsg}
          </div>
        )}

        {report && (
          <div className="space-y-3">
            <div className="text-xs text-slate-400">
              替换 <span className="text-slate-200">{report.original}</span>
              {report.substitute_group && ` · 可互换组 ${report.substitute_group}`} · 库内考察{" "}
              {report.total_considered} · 联网 {extMeta?.count ?? external.length}
            </div>
            {identity && (
              <div className="text-[11px] text-slate-500" data-testid="substitute-identity">
                鉴定：
                {identity.resolved
                  ? [
                      identity.cas_no ? `CAS ${identity.cas_no}` : null,
                      identity.smiles
                        ? `SMILES ${identity.smiles.length > 36 ? `${identity.smiles.slice(0, 36)}…` : identity.smiles}`
                        : null,
                      `source=${identity.source}`,
                    ]
                      .filter(Boolean)
                      .join(" · ")
                  : `未解析结构（${identity.source}）`}
              </div>
            )}

            <div>
              <div className="text-xs text-slate-300 mb-1">库内候选（优先）</div>
              {report.candidates.length === 0 ? (
                <div className="text-slate-500 text-xs">
                  目录中没有同组或同角色的替代品。可在「材料空间」新增材料，或从联网结果入库。
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
                  className={`text-xs mt-1 ${CONFIDENCE_STYLE[report.candidates[0].delta_confidence]}`}
                >
                  ⓘ {CONFIDENCE_NOTE[report.candidates[0].delta_confidence]}
                </div>
              )}
            </div>

            {includeExternal && (
              <div data-testid="external-substitutes-section">
                <div className="text-xs text-slate-300 mb-1">联网候选（结构相似 · 选用后可入库）</div>
                {extMeta?.skipped_reason && external.length === 0 ? (
                  <div className="text-slate-500 text-xs">{extMeta.skipped_reason}</div>
                ) : external.length === 0 ? (
                  <div className="text-slate-500 text-xs">暂无联网相似结果。</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead className="text-slate-400">
                        <tr>
                          <th className="text-left py-1">名称</th>
                          <th className="text-left">CAS</th>
                          <th className="text-right">相似</th>
                          <th className="text-left pl-2">来源</th>
                          <th className="text-right">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {external.map((row) => {
                          const rowKey = row.cid != null ? `cid-${row.cid}` : row.name;
                          return (
                            <tr key={rowKey} className="border-t border-edge/50 align-top">
                              <td className="py-1">
                                <div className="text-slate-200">{row.name}</div>
                                {row.iupac_name && row.iupac_name !== row.name && (
                                  <div className="text-slate-500 truncate max-w-[14rem]" title={row.iupac_name}>
                                    {row.iupac_name}
                                  </div>
                                )}
                                {row.in_catalog && (
                                  <div className="text-accent/80">已在库：{row.catalog_name}</div>
                                )}
                              </td>
                              <td className="text-slate-400">{row.cas_no || "—"}</td>
                              <td className="text-right">{(row.similarity * 100).toFixed(0)}%</td>
                              <td className="pl-2 text-slate-500">PubChem</td>
                              <td className="text-right">
                                {row.in_catalog ? (
                                  <span className="text-slate-500">见上方</span>
                                ) : (
                                  <button
                                    type="button"
                                    className="px-2 py-0.5 rounded border border-accent/40 text-accent
                                               hover:bg-accent/10 disabled:opacity-50"
                                    disabled={promoting === (row.cid != null ? String(row.cid) : row.name)}
                                    onClick={() => void promoteExternal(row)}
                                  >
                                    {promoting === (row.cid != null ? String(row.cid) : row.name)
                                      ? "…"
                                      : "入库并选用"}
                                  </button>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
                <div className="text-[11px] text-slate-500 mt-1">
                  ⓘ 联网项未做配方 Δ；入库后可再点「查找替代」查看偏离。聚合物/仅商品名若无法解析结构会显示原因。
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
