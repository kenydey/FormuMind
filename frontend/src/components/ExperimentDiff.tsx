import { type WorkbenchRow } from "../api";

interface ExperimentDiffProps {
  a: WorkbenchRow;
  b: WorkbenchRow;
  onClose: () => void;
}

function fmt(v: unknown): string {
  if (v == null || v === "") return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  return String(v);
}

function buildDiff(a: WorkbenchRow, b: WorkbenchRow) {
  const aFactors = { ...(a.planned_params || {}), ...(a.actual_params || {}) };
  const bFactors = { ...(b.planned_params || {}), ...(b.actual_params || {}) };
  const factorKeys = Array.from(
    new Set([...Object.keys(aFactors), ...Object.keys(bFactors)])
  );
  const factorDiff = factorKeys.map((k) => ({
    key: k,
    a: aFactors[k],
    b: bFactors[k],
    changed: aFactors[k] !== bFactors[k],
  }));

  const aMeas = a.measurements || {};
  const bMeas = b.measurements || {};
  const measKeys = Array.from(new Set([...Object.keys(aMeas), ...Object.keys(bMeas)]));
  const measDiff = measKeys.map((k) => ({
    key: k,
    a: aMeas[k],
    b: bMeas[k],
    changed: aMeas[k] !== bMeas[k],
  }));

  return { factorDiff, measDiff };
}

/**
 * Side-by-side formulation diff (Phase 3.3).
 *
 * A chemist asks "what changed between v3 and v4" constantly; this renders the
 * factor + measured deltas with changed rows highlighted.
 */
export default function ExperimentDiff({ a, b, onClose }: ExperimentDiffProps) {
  const { factorDiff, measDiff } = buildDiff(a, b);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-edge rounded-lg shadow-xl w-[42rem] max-w-[94vw] p-4 text-sm max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-slate-200">
            配方对比（行 #{a.id} vs #{b.id}）
          </h3>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>

        <table className="w-full text-xs">
          <thead>
            <tr className="text-slate-400">
              <th className="text-left py-1">配方参数</th>
              <th className="text-right">#{a.id}</th>
              <th className="text-right">#{b.id}</th>
            </tr>
          </thead>
          <tbody>
            {factorDiff.map((d) => (
              <tr
                key={d.key}
                className={`border-t border-edge/50 ${d.changed ? "bg-yellow-500/10" : ""}`}
              >
                <td className="py-1 text-slate-400">{d.key}</td>
                <td className={`text-right font-mono ${d.changed ? "text-yellow-300" : "text-slate-300"}`}>
                  {fmt(d.a)}
                </td>
                <td className={`text-right font-mono ${d.changed ? "text-yellow-300" : "text-slate-300"}`}>
                  {fmt(d.b)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {measDiff.length > 0 && (
          <>
            <h4 className="text-[10px] font-semibold text-slate-400 mt-4 mb-1">实测指标</h4>
            <table className="w-full text-xs">
              <tbody>
                {measDiff.map((d) => (
                  <tr
                    key={d.key}
                    className={`border-t border-edge/50 ${d.changed ? "bg-accent2/10" : ""}`}
                  >
                    <td className="py-1 text-slate-400">{d.key}</td>
                    <td className={`text-right font-mono ${d.changed ? "text-accent2" : "text-slate-300"}`}>
                      {fmt(d.a)}
                    </td>
                    <td className={`text-right font-mono ${d.changed ? "text-accent2" : "text-slate-300"}`}>
                      {fmt(d.b)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}
