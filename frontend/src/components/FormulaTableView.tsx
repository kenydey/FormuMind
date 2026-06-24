import type { Formulation, ProductDomain } from "../api";
import { OBJECTIVE_METRIC } from "../api";
import { copyFormulaJson, downloadFormulaCsv } from "../utils/export";

const METRIC_LABELS: Record<string, string> = {
  salt_spray_hours: "耐盐雾 (h)",
  cleaning_efficiency: "清洗率 (%)",
  cost_cny_per_kg: "成本 (CNY/kg)",
  voc_gpl: "VOC (g/L)",
  coating_weight_gsm: "膜重 (g/m²)",
};

function fmtMetric(form: Formulation, key: string): string {
  const v = form.predicted[key];
  if (v == null) return "—";
  const std = form.predicted_std?.[key];
  return std != null ? `${v.toFixed(1)}±${std.toFixed(1)}` : v.toFixed(2);
}

export default function FormulaTableView({
  forms,
  domain,
  onSelect,
}: {
  forms: Formulation[];
  domain: ProductDomain;
  onSelect?: (index: number) => void;
}) {
  const primary = OBJECTIVE_METRIC[domain];

  return (
    <div className="overflow-x-auto border border-edge rounded-lg">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-edge bg-ink/80 text-slate-400">
            <th className="text-left py-2 px-2 font-normal">#</th>
            <th className="text-left py-2 px-2 font-normal">名称</th>
            <th className="text-right py-2 px-2 font-normal">Score</th>
            <th className="text-right py-2 px-2 font-normal">{METRIC_LABELS[primary] ?? primary}</th>
            <th className="text-right py-2 px-2 font-normal">成本</th>
            <th className="text-right py-2 px-2 font-normal">VOC</th>
            <th className="text-right py-2 px-2 font-normal">成分</th>
            <th className="text-right py-2 px-2 font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          {forms.map((form, i) => (
            <tr
              key={`${form.name}-${i}`}
              className="border-b border-edge/40 hover:bg-accent/5 cursor-pointer"
              onClick={() => onSelect?.(i)}
            >
              <td className="py-2 px-2 font-mono text-accent2">{i + 1}</td>
              <td className="py-2 px-2 text-slate-200 max-w-[200px] truncate">{form.name}</td>
              <td className="py-2 px-2 text-right font-mono text-accent">
                {form.score != null ? form.score.toFixed(2) : "—"}
              </td>
              <td className="py-2 px-2 text-right font-mono text-slate-300">
                {fmtMetric(form, primary)}
              </td>
              <td className="py-2 px-2 text-right font-mono text-slate-300">
                {fmtMetric(form, "cost_cny_per_kg")}
              </td>
              <td className="py-2 px-2 text-right font-mono text-slate-300">
                {fmtMetric(form, "voc_gpl")}
              </td>
              <td className="py-2 px-2 text-right text-slate-400">{form.ingredients.length}</td>
              <td className="py-2 px-2 text-right">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    downloadFormulaCsv(form);
                  }}
                  className="text-[10px] border border-edge rounded px-1.5 py-0.5 text-slate-400 hover:text-accent"
                >
                  CSV
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void copyFormulaJson(form);
                  }}
                  className="ml-1 text-[10px] border border-edge rounded px-1.5 py-0.5 text-slate-400 hover:text-accent"
                >
                  JSON
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
