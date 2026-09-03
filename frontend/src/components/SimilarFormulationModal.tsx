import { useState, useEffect } from "react";
import { api, formatApiError, type Formulation } from "../api";

interface SimilarMatch {
  experiment_id: number;
  project_id: string;
  project_title: string | null;
  similarity: number;
  factors: Record<string, number>;
  measured: Record<string, number>;
  shared_ingredients: string[];
  differing_ingredients: string[];
}

interface SimilarFormulationModalProps {
  formulation: Formulation;
  onClose: () => void;
}

export default function SimilarFormulationModal({ formulation, onClose }: SimilarFormulationModalProps) {
  const [matches, setMatches] = useState<SimilarMatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const factors: Record<string, number> = {};
    for (const [k, v] of Object.entries(formulation.factors ?? {})) {
      if (typeof v === "number") factors[k] = v;
    }
    if (Object.keys(factors).length === 0) {
      setLoading(false);
      return;
    }
    api.kgSimilarFormulations(factors, 10)
      .then((res) => setMatches(res.matches || []))
      .catch((err) => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  }, [formulation]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="w-full max-w-2xl max-h-[80vh] overflow-auto rounded-xl border border-edge bg-panel p-4 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-200">相似历史配方 · {formulation.name}</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300 text-lg">&times;</button>
        </div>
        <div className="mb-3 p-2 rounded bg-ink/60 border border-edge/30 text-[11px]">
          <span className="text-slate-500">当前配方: </span>
          {Object.entries(formulation.factors ?? {}).map(([k, v]) => (
            <span key={k} className="text-slate-300 mr-2">{k}: {v}%</span>
          ))}
        </div>
        {loading && <div className="text-slate-500 text-sm py-4">搜索相似配方中...</div>}
        {error && <div className="text-red-400 text-sm py-2">{error}</div>}
        {!loading && matches.length === 0 && <div className="text-slate-500 text-sm py-4">未找到相似历史配方</div>}
        <div className="space-y-2">
          {matches.map((m) => (
            <div key={m.experiment_id} className="rounded-lg border border-edge bg-ink/40 p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium text-accent">相似度: {(m.similarity * 100).toFixed(1)}%</span>
                <span className="text-[10px] text-slate-500">{m.project_title || m.project_id} · 实验 #{m.experiment_id}</span>
              </div>
              <div className="text-[11px] text-slate-300 mb-1">
                {Object.entries(m.factors).map(([k, v]) => (
                  <span key={k} className={`mr-2 ${m.shared_ingredients.includes(k) ? "text-emerald-400" : "text-amber-400"}`}>
                    {k}: {v}%
                  </span>
                ))}
              </div>
              {Object.keys(m.measured).length > 0 && (
                <div className="text-[10px] text-slate-500">实测: {Object.entries(m.measured).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>
              )}
              {m.shared_ingredients.length > 0 && (
                <div className="text-[10px] text-emerald-500 mt-1">共同成分: {m.shared_ingredients.join(", ")}</div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
