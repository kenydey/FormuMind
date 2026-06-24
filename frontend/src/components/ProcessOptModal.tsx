import { useState } from "react";
import { LineChart, Line, Tooltip, ResponsiveContainer, XAxis, YAxis } from "recharts";
import { useStore } from "../store";
import { api } from "../api";

const PARAM_LABELS: Record<string, string> = {
  cure_temperature_c: "固化温度 (°C)",
  cure_time_min: "固化时间 (min)",
  dispersion_rpm: "分散转速 (rpm)",
  film_thickness_um: "膜厚 (μm)",
  bath_temperature_c: "浴温 (°C)",
  immersion_time_min: "浸泡时间 (min)",
  ph_setpoint: "pH 设定值",
  treat_temperature_c: "处理温度 (°C)",
  accelerator_factor: "促进剂倍数 (×)",
};

const OUTCOME_LABELS: Record<string, string> = {
  cure_conversion_pct: "固化转化率 (%)",
  salt_spray_improvement_h: "盐雾改善 (h)",
  film_uniformity_pct: "涂膜均匀性 (%)",
  film_thickness_um: "干膜厚度 (μm)",
  cleaning_efficiency_pct: "清洗效率 (%)",
  foam_index: "泡沫指数",
  bath_temperature_c: "浴温 (°C)",
  coating_weight_gsm: "膜重 (g/m²)",
  adhesion_promotion_idx: "附着力促进指数",
  treat_temperature_c: "处理温度 (°C)",
};

export default function ProcessOptModal() {
  const { requirement, processOptResult, setProcessOptResult } = useStore();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [iterations, setIterations] = useState(18);

  const result = processOptResult;

  const run = async () => {
    if (!requirement) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.optimizeProcess({ domain: requirement.domain, iterations });
      setProcessOptResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  };

  const chartData = result?.history.map((v, i) => ({ iter: i + 1, score: v })) ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <label className="text-xs text-slate-400">
          迭代次数
          <input
            type="number"
            value={iterations}
            onChange={(e) => setIterations(Math.max(4, parseInt(e.target.value) || 18))}
            className="ml-2 w-16 bg-ink border border-edge rounded px-2 py-0.5 text-xs text-slate-200 font-mono"
            min={4}
            max={100}
          />
        </label>
        <button
          onClick={run}
          disabled={loading || !requirement}
          className="border border-accent2 text-accent2 hover:bg-accent2/10 rounded px-3 py-1.5 text-xs disabled:opacity-40"
        >
          {loading ? "优化中…" : "运行工艺参数优化"}
        </button>
      </div>

      {!requirement && (
        <p className="text-amber-400 text-xs">请先在"技术需求"中设置产品域。</p>
      )}
      {error && <p className="text-red-400 text-sm">{error}</p>}

      {result && (
        <div className="space-y-4">
          <div className="flex items-center gap-3 text-xs text-slate-400">
            <span>引擎：<span className="font-mono text-accent2">{result.engine}</span></span>
            <span>迭代：<span className="font-mono text-accent2">{result.iterations}</span></span>
          </div>

          {chartData.length > 1 && (
            <div className="h-32">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
                  <XAxis dataKey="iter" tick={{ fontSize: 9, fill: "#64748b" }} />
                  <YAxis tick={{ fontSize: 9, fill: "#64748b" }} width={30} />
                  <Tooltip
                    contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 10, padding: "2px 6px" }}
                    formatter={(v: number) => [v.toFixed(3), "最优分"]}
                  />
                  <Line type="monotone" dataKey="score" stroke="#38bdf8" strokeWidth={1.5} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <h4 className="text-xs uppercase tracking-widest text-slate-400 mb-2">最优工艺参数</h4>
              <div className="space-y-1">
                {Object.entries(result.best_params).map(([k, v]) => (
                  <div key={k} className="flex justify-between text-xs bg-edge/40 rounded px-2 py-1">
                    <span className="text-slate-400">{PARAM_LABELS[k] ?? k}</span>
                    <span className="font-mono text-accent2">{v.toFixed(1)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <h4 className="text-xs uppercase tracking-widest text-slate-400 mb-2">预测性能</h4>
              <div className="space-y-1">
                {Object.entries(result.predicted_outcome).map(([k, v]) => (
                  <div key={k} className="flex justify-between text-xs bg-edge/40 rounded px-2 py-1">
                    <span className="text-slate-400">{OUTCOME_LABELS[k] ?? k}</span>
                    <span className="font-mono text-accent2">{v.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {!result && !loading && (
        <p className="text-slate-500 text-sm">
          工艺参数优化独立于配方组成，优化固化温度、膜厚、分散转速等制造参数。
        </p>
      )}
    </div>
  );
}
