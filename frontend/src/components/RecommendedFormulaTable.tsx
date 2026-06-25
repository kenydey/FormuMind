import type { Ingredient } from "../api";

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

export default function RecommendedFormulaTable({ ingredients }: { ingredients: Ingredient[] }) {
  if (!ingredients.length) {
    return <p className="text-xs text-slate-500">无组分数据</p>;
  }

  return (
    <div className="overflow-x-auto border border-edge/60 rounded">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="border-b border-edge bg-ink/80 text-slate-400">
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">组分类型</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">化合物名称</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">结构式</th>
            <th className="text-right py-1.5 px-2 font-normal whitespace-nowrap">分子量</th>
            <th className="text-right py-1.5 px-2 font-normal whitespace-nowrap">设定当量</th>
            <th className="text-right py-1.5 px-2 font-normal whitespace-nowrap">摩尔量</th>
            <th className="text-right py-1.5 px-2 font-normal whitespace-nowrap">称重/体积</th>
            <th className="text-left py-1.5 px-2 font-normal whitespace-nowrap">作用/备注</th>
          </tr>
        </thead>
        <tbody>
          {ingredients.map((ing) => (
            <tr key={ing.name} className="border-b border-edge/40 align-top">
              <td className="py-1 px-2 text-slate-400 whitespace-nowrap">{componentTypeLabel(ing)}</td>
              <td className="py-1 px-2 text-slate-200">{ing.name}</td>
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
              <td className="py-1 px-2 text-right font-mono text-slate-300">
                {ing.equivalents != null ? ing.equivalents.toFixed(2) : "—"}
              </td>
              <td className="py-1 px-2 text-right font-mono text-slate-300">
                {ing.mmol != null ? ing.mmol.toFixed(2) : "—"}
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
