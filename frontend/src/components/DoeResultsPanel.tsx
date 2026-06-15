import type { ModelInfo } from "../api";
import { OBJECTIVE_METRIC } from "../api";
import { useStore } from "../store";

function R2Gauge({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value));
  const angle = pct * 180;
  const r = 20;
  const cx = 28, cy = 28;
  const toXY = (deg: number) => {
    const rad = ((deg - 180) * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  };
  const start = toXY(0);
  const end = toXY(angle);
  const large = angle > 180 ? 1 : 0;
  const color = pct > 0.85 ? "#34d399" : pct > 0.6 ? "#fbbf24" : "#f87171";
  return (
    <svg width="56" height="32" viewBox="0 0 56 36" className="shrink-0">
      <path d={`M ${toXY(0).x} ${toXY(0).y} A ${r} ${r} 0 0 1 ${toXY(180).x} ${toXY(180).y}`}
        fill="none" stroke="#1e293b" strokeWidth="5" strokeLinecap="round" />
      {pct > 0.01 && (
        <path d={`M ${start.x} ${start.y} A ${r} ${r} 0 ${large} 1 ${end.x} ${end.y}`}
          fill="none" stroke={color} strokeWidth="5" strokeLinecap="round" />
      )}
      <text x={cx} y={cy + 8} textAnchor="middle" fill={color} fontSize="9" fontFamily="monospace">
        {(value * 100).toFixed(0)}%
      </text>
    </svg>
  );
}

function ModelCard({ m }: { m: ModelInfo }) {
  return (
    <div className="flex items-center gap-2 border border-edge/40 rounded p-1.5 bg-ink/40">
      <R2Gauge value={m.r2} />
      <div className="text-[10px] leading-snug">
        <div className="text-accent2 font-mono">{m.metric}</div>
        <div className="text-slate-500">{m.backend} · n={m.n_samples}</div>
        <div className="text-slate-500">RMSE={m.rmse}{m.cv_r2 != null ? ` · cvR²=${m.cv_r2}` : ""}</div>
      </div>
    </div>
  );
}

const DESIGNS = [
  { value: "full_factorial", label: "全因子" },
  { value: "fractional_factorial", label: "部分因子" },
  { value: "plackett_burman", label: "Plackett-Burman" },
  { value: "ccd", label: "中心复合 CCD" },
  { value: "lhs", label: "拉丁超立方" },
];

export default function DoeResultsPanel() {
  const { requirement, doePlan, measured, models, trainMessage, busy, generateDoe, setMeasured, submitResults } =
    useStore();
  const metric = OBJECTIVE_METRIC[requirement.domain];

  return (
    <section className="glass rounded-xl p-4 overflow-y-auto">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm uppercase tracking-widest text-accent2">DOE 实验结果回灌 · Feedback</h2>
        <div className="flex gap-2">
          <select
            id="doe-design"
            defaultValue="ccd"
            className="bg-ink border border-edge rounded px-2 py-1 text-xs"
          >
            {DESIGNS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
          <button
            disabled={busy !== "idle"}
            onClick={() => generateDoe((document.getElementById("doe-design") as HTMLSelectElement).value)}
            className="text-xs border border-accent text-accent rounded px-2 py-1 hover:bg-accent/10 disabled:opacity-40"
          >
            {busy === "doe" ? "生成中…" : "生成 DOE"}
          </button>
        </div>
      </div>

      {models.length > 0 && (
        <div className="mb-3 grid grid-cols-2 gap-1.5">
          {models.map((m) => (
            <ModelCard key={`${m.domain}-${m.metric}`} m={m} />
          ))}
        </div>
      )}
      {trainMessage && <div className="text-[11px] text-slate-500 mb-2">{trainMessage}</div>}

      {!doePlan ? (
        <p className="text-slate-500 text-sm">
          生成 DOE 设计后，在此填入每个实验的实测 <span className="text-accent font-mono">{metric}</span>，回灌训练数据驱动的预测模型。
        </p>
      ) : (
        <>
          <div className="text-[11px] text-slate-500 mb-2">{doePlan.notes}</div>
          <div className="max-h-56 overflow-y-auto border border-edge rounded">
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-panel">
                <tr className="text-slate-400">
                  <th className="text-left px-2 py-1">#</th>
                  {doePlan.factors.map((f) => (
                    <th key={f.name} className="text-right px-2 py-1 font-normal">
                      {f.name.replace(" (DGEBA)", "").slice(0, 10)}
                    </th>
                  ))}
                  <th className="text-right px-2 py-1 text-accent">实测 {metric}</th>
                </tr>
              </thead>
              <tbody>
                {doePlan.runs.map((run) => (
                  <tr key={run.run_id} className="border-t border-edge/40">
                    <td className="px-2 py-1 text-slate-500">{run.run_id}</td>
                    {doePlan.factors.map((f) => (
                      <td key={f.name} className="text-right px-2 py-1 font-mono text-slate-300">
                        {run.natural[f.name]}
                      </td>
                    ))}
                    <td className="px-2 py-1 text-right">
                      <input
                        type="number"
                        className="w-20 bg-ink border border-edge rounded px-1 py-0.5 text-right text-accent2"
                        value={measured[run.run_id] ?? ""}
                        onChange={(e) =>
                          setMeasured(run.run_id, e.target.value === "" ? NaN : Number(e.target.value))
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            disabled={busy !== "idle"}
            onClick={submitResults}
            className="mt-3 w-full bg-accent2/90 hover:bg-accent2 text-ink font-semibold rounded px-3 py-2 text-sm disabled:opacity-40"
          >
            {busy === "training" ? "训练中…" : "③ 回灌实验结果并训练模型"}
          </button>
        </>
      )}
    </section>
  );
}
