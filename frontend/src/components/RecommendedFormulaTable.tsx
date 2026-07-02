import { useState } from "react";
import type { Ingredient } from "../api";
import { api } from "../api";

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

export default function RecommendedFormulaTable({
  ingredients,
  editable,
  onIngredientChange,
}: {
  ingredients: Ingredient[];
  editable?: boolean;
  onIngredientChange?: (idx: number, patch: Partial<Ingredient>) => void;
}) {
  const [lookupBusy, setLookupBusy] = useState<number | null>(null);

  async function lookupCas(idx: number, query: string) {
    if (!query.trim() || !onIngredientChange) return;
    setLookupBusy(idx);
    try {
      const hit = await api.chemicalLookup(query.trim());
      onIngredientChange(idx, {
        cas_no: hit.cas || undefined,
        name: hit.iupac_name || query,
        zh_name: hit.zh_name || ingredients[idx]?.zh_name,
        formula: hit.formula || ingredients[idx]?.formula,
        molar_mass: hit.molar_mass ?? ingredients[idx]?.molar_mass,
        smiles: hit.smiles ?? ingredients[idx]?.smiles,
      });
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
          {ingredients.map((ing, idx) => (
            <tr key={`${ing.name}-${idx}`} className="border-b border-edge/40 align-top">
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
                  <span className="font-mono text-slate-400">{ing.cas_no || "—"}</span>
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
              </td>
              <td className="py-1 px-2 text-right font-mono text-slate-300">
                {ing.molar_mass != null ? ing.molar_mass.toFixed(2) : "—"}
              </td>
              <td className="py-1 px-2 text-right font-mono text-accent2">
                {ing.amount_display || `${ing.weight_pct}%`}
              </td>
              <td className="py-1 px-2 text-slate-400 max-w-[140px]">{ing.notes || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
