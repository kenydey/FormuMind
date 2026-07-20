import { useState } from "react";
import type { ChemicalProfile, Ingredient } from "../api";
import { api } from "../api";

const CAS_RE = /^(\d{2,7})-(\d{2})-(\d)$/;

function casChecksumOk(cas: string): boolean {
  const m = CAS_RE.exec((cas || "").trim());
  if (!m) return false;
  const digits = `${m[1]}${m[2]}`;
  const total = [...digits].reverse().reduce((sum, d, i) => sum + Number(d) * (i + 1), 0);
  return total % 10 === Number(m[3]);
}

function ingredientLabels(ing: Ingredient): string[] {
  return [ing.zh_name, ing.name].filter(Boolean) as string[];
}

function casEnrichStatus(
  ing: Ingredient,
  validateWarnings: string[]
): "missing" | "invalid" | "corrected" | "ok" {
  const labels = ingredientLabels(ing);
  const ingWarnings = validateWarnings.filter((w) =>
    labels.some((label) => w.startsWith(`${label}:`))
  );
  if (ingWarnings.some((w) => w.includes("校验失败"))) return "invalid";
  if (ingWarnings.some((w) => w.includes("不一致") || w.includes("已采用"))) return "corrected";
  const cas = (ing.cas_no || "").trim();
  if (!cas) return "missing";
  if (!casChecksumOk(cas)) return "invalid";
  return "ok";
}

function CasBadge({ status }: { status: ReturnType<typeof casEnrichStatus> }) {
  if (status === "ok") return null;
  const cfg =
    status === "missing"
      ? {
          label: "缺 CAS",
          cls: "border-amber-500/40 bg-amber-500/10 text-amber-300",
          title: "未识别 CAS，可在编辑模式下手动查询补全",
        }
      : status === "invalid"
        ? {
            label: "CAS 无效",
            cls: "border-red-500/40 bg-red-500/10 text-red-300",
            title: "CAS 校验失败，后端 enrich 已忽略该值",
          }
        : {
            label: "已校正",
            cls: "border-teal-500/40 bg-teal-500/10 text-teal-300",
            title: "CAS 已与目录/检索结果对齐",
          };
  return (
    <span
      title={cfg.title}
      className={`ml-1 text-[9px] px-1 py-0.5 rounded border whitespace-nowrap ${cfg.cls}`}
    >
      {cfg.label}
    </span>
  );
}

const ROLE_LABELS: Record<string, string> = {
  resin: "树脂",
  hardener: "固化剂",
  inhibitor: "缓蚀剂",
  solvent: "溶剂",
  pigment: "颜料",
  surfactant: "表面活性剂",
  additive: "助剂",
};

function componentTypeLabel(ing: Ingredient): string {
  return ing.component_type || ROLE_LABELS[ing.role] || ing.role || "组分";
}

function ChemBadges({ profile }: { profile: ChemicalProfile | undefined }) {
  if (!profile) return null;
  const badges: { label: string; cls: string; title: string }[] = [];
  if (profile.patented === true) {
    badges.push({
      label: "🔒 专利",
      cls: "border-amber-500/40 bg-amber-500/10 text-amber-300",
      title: "分子结构已见于专利文献（molbloom 预筛），建议开展 FTO 检索",
    });
  }
  if (profile.safety?.controlled === true) {
    badges.push({
      label: "⚠ 管制",
      cls: "border-red-500/40 bg-red-500/10 text-red-300",
      title: "命中管制化学品清单，采购/使用需合规确认",
    });
  }
  if (profile.safety?.explosive === true) {
    badges.push({
      label: "💥 爆炸性",
      cls: "border-red-500/40 bg-red-500/10 text-red-300",
      title: "GHS 爆炸性危害标识",
    });
  }
  for (const g of (profile.func_groups || []).slice(0, 3)) {
    badges.push({
      label: g,
      cls: "border-edge bg-ink/60 text-slate-400",
      title: `官能团：${g}`,
    });
  }
  if (!badges.length) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-0.5">
      {badges.map((b, i) => (
        <span
          key={i}
          title={b.title}
          className={`text-[9px] px-1 py-0.5 rounded border whitespace-nowrap ${b.cls}`}
        >
          {b.label}
        </span>
      ))}
    </div>
  );
}

export default function RecommendedFormulaTable({
  ingredients,
  editable,
  onIngredientChange,
  validateWarnings = [],
}: {
  ingredients: Ingredient[];
  editable?: boolean;
  onIngredientChange?: (idx: number, patch: Partial<Ingredient>) => void;
  validateWarnings?: string[];
}) {
  const [lookupBusy, setLookupBusy] = useState<number | null>(null);
  const [profiles, setProfiles] = useState<Record<number, ChemicalProfile>>({});

  async function lookupCas(idx: number, query: string) {
    if (!query.trim() || !onIngredientChange) return;
    setLookupBusy(idx);
    try {
      const hit = await api.chemicalProfile(query.trim());
      onIngredientChange(idx, {
        cas_no: hit.cas || undefined,
        name: hit.iupac_name || query,
        zh_name: hit.zh_name || ingredients[idx]?.zh_name,
        formula: hit.formula || ingredients[idx]?.formula,
        molar_mass: hit.molar_mass ?? ingredients[idx]?.molar_mass,
        smiles: hit.smiles ?? ingredients[idx]?.smiles,
      });
      setProfiles((prev) => ({ ...prev, [idx]: hit }));
    } catch {
      onIngredientChange(idx, { cas_no: query });
    } finally {
      setLookupBusy(null);
    }
  }

  if (!ingredients.length) {
    return <p className="text-xs text-slate-500">无组分数据</p>;
  }

  return (
    <div className="overflow-x-auto border border-edge/60 rounded">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="border-b border-edge bg-ink/80 text-slate-400">
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">组分类型</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">中文名称</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">英文名称</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">CAS No.</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">结构式</th>
            <th className="text-right py-1.5 px-2 font-normal whitespace-nowrap">分子量</th>
            <th className="text-right py-1.5 px-2 font-normal whitespace-nowrap">称重/体积</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">备注</th>
          </tr>
        </thead>
        <tbody>
          {ingredients.map((ing, idx) => {
            const casStatus = casEnrichStatus(ing, validateWarnings);
            const lowGrounding = ing.grounding_confidence === "low";
            return (
            <tr
              key={`${ing.name}-${idx}`}
              className={`border-b border-edge/40 align-top ${
                lowGrounding ? "bg-amber-500/[0.06] ring-1 ring-inset ring-amber-500/15" : ""
              }`}
            >
              <td className="py-1 px-2 text-slate-400 whitespace-nowrap">{componentTypeLabel(ing)}</td>
              <td className="py-1 px-2">
                {editable && onIngredientChange ? (
                  <input
                    value={ing.zh_name ?? ""}
                    onChange={(e) => onIngredientChange(idx, { zh_name: e.target.value })}
                    className="w-full min-w-[80px] bg-ink border border-edge rounded px-1 py-0.5 text-slate-200"
                    placeholder="中文名"
                  />
                ) : (
                  <span className="text-slate-300">{ing.zh_name || "—"}</span>
                )}
              </td>
              <td className="py-1 px-2 text-slate-200">
                {editable && onIngredientChange ? (
                  <input
                    value={ing.name}
                    onChange={(e) => onIngredientChange(idx, { name: e.target.value })}
                    className="w-full min-w-[100px] bg-ink border border-edge rounded px-1 py-0.5"
                  />
                ) : (
                  ing.name
                )}
              </td>
              <td className="py-1 px-2">
                {editable && onIngredientChange ? (
                  <input
                    value={ing.cas_no ?? ""}
                    onChange={(e) => onIngredientChange(idx, { cas_no: e.target.value })}
                    onBlur={(e) => void lookupCas(idx, e.target.value || ing.name)}
                    className="w-full min-w-[90px] bg-ink border border-edge rounded px-1 py-0.5 font-mono text-[10px]"
                    placeholder={lookupBusy === idx ? "查询中…" : "CAS"}
                  />
                ) : (
                  <span className="font-mono text-slate-400">
                    {ing.cas_no || "—"}
                    <CasBadge status={casStatus} />
                  </span>
                )}
              </td>
              <td className="py-1 px-2 min-w-[88px]">
                {ing.smiles ? (
                  <span className="font-mono text-[10px] text-slate-500 break-all" title={ing.smiles}>
                    {ing.smiles.slice(0, 24)}
                    {ing.smiles.length > 24 ? "…" : ""}
                  </span>
                ) : (
                  <span className="font-mono text-slate-500">{ing.mf_structure || ing.formula || "—"}</span>
                )}
                <ChemBadges profile={profiles[idx]} />
              </td>
              <td className="py-1 px-2 text-right font-mono text-slate-300">
                {ing.molar_mass != null ? ing.molar_mass.toFixed(2) : "—"}
              </td>
              <td className="py-1 px-2 text-right font-mono text-accent2">
                {ing.amount_display || `${ing.weight_pct}%`}
              </td>
              <td className="py-1 px-2 text-slate-400 text-[10px] max-w-[140px]">
                {ing.notes || "—"}
                {lowGrounding && (
                  <span
                    className="ml-1 text-amber-400 border border-amber-500/40 bg-amber-500/10 rounded px-1"
                    title="缺少文献/专利依据，建议补充检索或手动核对"
                  >
                    低可信
                  </span>
                )}
                {(ing.evidence_refs?.length ?? 0) > 0 && (
                  <div className="text-[9px] text-teal-400/90 mt-0.5 truncate" title={ing.evidence_refs?.join(", ")}>
                    引用: {ing.evidence_refs?.slice(0, 2).join(", ")}
                  </div>
                )}
              </td>
            </tr>
          );
          })}
        </tbody>
      </table>
    </div>
  );
}
