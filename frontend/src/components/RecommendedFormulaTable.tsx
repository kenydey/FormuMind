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
  onIngredientChange,
}: {
  ingredients: Ingredient[];
  onIngredientChange?: (index: number, ing: Ingredient) => void;
}) {
  const [lookupBusy, setLookupBusy] = useState<number | null>(null);

  async function lookupAndFill(index: number, query: string) {
    if (!onIngredientChange || !query.trim()) return;
    setLookupBusy(index);
    try {
      const result = await api.lookupChemical(query.trim());
      const ing = ingredients[index];
      onIngredientChange(index, {
        ...ing,
        name: result.iupac_name || ing.name,
        zh_name: result.zh_name || ing.zh_name,
        cas_no: result.cas || ing.cas_no,
        formula: result.formula || ing.formula,
        mf_structure: result.formula || ing.mf_structure,
        smiles: result.smiles || ing.smiles,
        molar_mass: result.molar_mass ?? ing.molar_mass,
      });
    } catch {
      // best-effort lookup
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
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">化合物名称</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">CAS No.</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">结构式</th>
            <th className="text-right py-1.5 px-2 font-normal whitespace-nowrap">分子量</th>
            <th className="text-right py-1.5 px-2 font-normal whitespace-nowrap">设定当量</th>
            <th className="text-right py-1.5 px-2 font-normal whitespace-nowrap">摩尔量</th>
            <th className="text-right py-1.5 px-2 font-normal whitespace-nowrap">称重/体积</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">作用/备注</th>
          </tr>
        </thead>
        <tbody>
          {ingredients.map((ing, idx) => (
            <tr key={`${ing.name}-${idx}`} className="border-b border-edge/40 align-top">
              <td className="py-1 px-2 text-slate-400 whitespace-nowrap">{componentTypeLabel(ing)}</td>
              <td className="py-1 px-2">
                <input
                  value={ing.zh_name ?? ""}
                  onChange={(e) =>
                    onIngredientChange?.(idx, { ...ing, zh_name: e.target.value })
                  }
                  className="w-full min-w-[72px] bg-ink border border-edge/60 rounded px-1 py-0.5 text-slate-200"
                  placeholder="中文名"
                />
              </td>
              <td className="py-1 px-2 text-slate-200">
                <input
                  value={ing.name}
                  onChange={(e) => onIngredientChange?.(idx, { ...ing, name: e.target.value })}
                  onBlur={(e) => void lookupAndFill(idx, e.target.value)}
                  className="w-full min-w-[100px] bg-ink border border-edge/60 rounded px-1 py-0.5"
                />
              </td>
              <td className="py-1 px-2">
                <input
                  value={ing.cas_no ?? ""}
                  onChange={(e) =>
                    onIngredientChange?.(idx, { ...ing, cas_no: e.target.value })
                  }
                  onBlur={(e) => void lookupAndFill(idx, e.target.value)}
                  className="w-full min-w-[88px] bg-ink border border-edge/60 rounded px-1 py-0.5 font-mono text-[10px]"
                  placeholder={lookupBusy === idx ? "查询中…" : "CAS"}
                />
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
              <td className="py-1 px-2 text-right font-mono text-slate-400">
                {ing.molar_mass != null ? ing.molar_mass.toFixed(1) : "—"}
              </td>
              <td className="py-1 px-2 text-right font-mono text-slate-400">
                {ing.equivalents != null ? ing.equivalents : "—"}
              </td>
              <td className="py-1 px-2 text-right font-mono text-slate-400">
                {ing.mmol != null ? ing.mmol.toFixed(2) : "—"}
              </td>
              <td className="py-1 px-2 text-right text-slate-300">
                {ing.amount_display || (ing.weight_pct != null ? `${ing.weight_pct}%` : "—")}
              </td>
              <td className="py-1 px-2 text-slate-500">{ing.notes || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
