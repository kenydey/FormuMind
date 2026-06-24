import type { LeverSpec } from "../api";

export default function LeversEditor({
  levers,
  onChange,
}: {
  levers: LeverSpec[];
  onChange: (levers: LeverSpec[]) => void;
}) {
  function update(idx: number, patch: Partial<LeverSpec>) {
    onChange(levers.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  }

  function remove(idx: number) {
    onChange(levers.filter((_, i) => i !== idx));
  }

  function add() {
    onChange([...levers, { name: "New factor", low: 0, high: 10, unit: "wt%" }]);
  }

  return (
    <div className="mb-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-400 uppercase tracking-wider">DOE 因子 · Levers</span>
        <button
          type="button"
          onClick={add}
          className="text-[10px] text-slate-500 hover:text-accent border border-edge hover:border-accent/40 rounded px-1.5 py-0.5"
        >
          + 添加因子
        </button>
      </div>
      {levers.length === 0 ? (
        <p className="text-[11px] text-slate-500">未定义因子时将自动从当前配方推导。</p>
      ) : (
        <div className="flex flex-col gap-2">
          {levers.map((l, idx) => (
            <div key={`${l.name}-${idx}`} className="bg-ink/60 border border-edge rounded p-2 grid grid-cols-2 gap-2 text-xs">
              <input
                value={l.name}
                onChange={(e) => update(idx, { name: e.target.value })}
                className="col-span-2 bg-ink border border-edge rounded px-2 py-1"
                placeholder="因子名称"
              />
              <label className="flex flex-col gap-0.5">
                <span className="text-[10px] text-slate-500">下限</span>
                <input
                  type="number"
                  value={l.low}
                  onChange={(e) => update(idx, { low: Number(e.target.value) })}
                  className="bg-ink border border-edge rounded px-2 py-1 font-mono"
                />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[10px] text-slate-500">上限</span>
                <input
                  type="number"
                  value={l.high}
                  onChange={(e) => update(idx, { high: Number(e.target.value) })}
                  className="bg-ink border border-edge rounded px-2 py-1 font-mono"
                />
              </label>
              <input
                value={l.unit ?? "wt%"}
                onChange={(e) => update(idx, { unit: e.target.value })}
                className="bg-ink border border-edge rounded px-2 py-1 text-[10px]"
                placeholder="单位"
              />
              <button
                type="button"
                onClick={() => remove(idx)}
                className="text-rose-400 hover:text-rose-300 text-[10px] justify-self-end"
              >
                删除
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
