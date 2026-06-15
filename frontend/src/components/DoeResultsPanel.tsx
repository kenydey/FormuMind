import { OBJECTIVE_METRIC } from "../api";
import { useStore } from "../store";

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
        <div className="mb-3 text-[11px] text-slate-300 space-y-1">
          {models.map((m) => (
            <div key={`${m.domain}-${m.metric}`} className="flex flex-wrap gap-2 items-center">
              <span className="text-accent2 font-mono">{m.metric}</span>
              <span className="bg-edge px-1.5 py-0.5 rounded">{m.backend}</span>
              <span>n={m.n_samples}</span>
              <span>R²={m.r2}</span>
              {m.cv_r2 != null && <span>cvR²={m.cv_r2}</span>}
              <span>RMSE={m.rmse}</span>
            </div>
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
