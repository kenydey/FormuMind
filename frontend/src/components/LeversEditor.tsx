import type { LeverSpec } from "../api";

export default function LeversEditor({
  levers,
  onChange,
  disabled,
}: {
  levers: LeverSpec[];
  onChange: (levers: LeverSpec[]) => void;
  disabled?: boolean;
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

  function setKind(idx: number, kind: string) {
    const l = levers[idx];
    if (kind === "discrete") {
      update(idx, { kind, levels: l.levels && l.levels.length >= 2 ? l.levels : ["A", "B"] });
    } else {
      const { kind: _k, levels: _lv, ...rest } = l as LeverSpec & { kind?: string; levels?: unknown };
      onChange(levers.map((x, i) => (i === idx ? (rest as LeverSpec) : x)));
    }
  }

  return (
    <div className="mb-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-400 uppercase tracking-wider">DOE 因子 · Levers</span>
        <button
          type="button"
          onClick={add}
          disabled={disabled}
          className="text-[10px] text-slate-500 hover:text-accent border border-edge hover:border-accent/40 rounded px-1.5 py-0.5 disabled:opacity-40"
        >
          + 添加因子
        </button>
      </div>
      {levers.length === 0 ? (
        <p className="text-[11px] text-slate-500">
          未定义因子时将自动从当前配方推导。建议为当前基材显式定义 g/L 或 wt% 因子。
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {levers.map((l, idx) => (
            <div key={`${l.name}-${idx}`} className="bg-ink/60 border border-edge rounded p-2 grid grid-cols-2 gap-2 text-xs">
              <input
                value={l.name}
                disabled={disabled}
                onChange={(e) => update(idx, { name: e.target.value })}
                className="col-span-2 bg-ink border border-edge rounded px-2 py-1 disabled:opacity-50"
                placeholder="因子名称"
              />
              <label className="flex flex-col gap-0.5">
                <span className="text-[10px] text-slate-500">下限</span>
                <input
                  type="number"
                  value={l.low}
                  disabled={disabled}
                  onChange={(e) => update(idx, { low: Number(e.target.value) })}
                  className="bg-ink border border-edge rounded px-2 py-1 font-mono disabled:opacity-50"
                />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[10px] text-slate-500">上限</span>
                <input
                  type="number"
                  value={l.high}
                  disabled={disabled}
                  onChange={(e) => update(idx, { high: Number(e.target.value) })}
                  className="bg-ink border border-edge rounded px-2 py-1 font-mono disabled:opacity-50"
                />
              </label>
              <input
                value={l.unit ?? "wt%"}
                disabled={disabled}
                onChange={(e) => update(idx, { unit: e.target.value })}
                className="bg-ink border border-edge rounded px-2 py-1 text-[10px] disabled:opacity-50"
                placeholder="单位"
              />
              {/* Up-4A: 离散因子类型切换 */}
              <label className="flex flex-col gap-0.5">
                <span className="text-[10px] text-slate-500">类型</span>
                <select
                  value={l.kind ?? "continuous"}
                  disabled={disabled}
                  onChange={(e) => setKind(idx, e.target.value)}
                  className="bg-ink border border-edge rounded px-2 py-1 text-[11px] disabled:opacity-50"
                >
                  <option value="continuous">连续</option>
                  <option value="discrete">离散</option>
                </select>
              </label>
              {(l.kind ?? "continuous") === "discrete" ? (
                <label className="flex flex-col gap-0.5 col-span-2">
                  <span className="text-[10px] text-slate-500">水平（逗号分隔，至少 2 个）</span>
                  <input
                    value={(l.levels ?? []).join(", ")}
                    disabled={disabled}
                    onChange={(e) => {
                      const parts = e.target.value
                        .split(",")
                        .map((s) => s.trim())
                        .filter((s) => s.length > 0)
                        .map((s) => {
                          const n = Number(s);
                          return s !== "" && !Number.isNaN(n) ? n : s;
                        });
                      update(idx, { levels: parts });
                    }}
                    className="bg-ink border border-edge rounded px-2 py-1 font-mono text-[11px] disabled:opacity-50"
                    placeholder="例如：聚酰胺, 酚醛 或 1, 2, 3"
                  />
                </label>
              ) : null}
              <button
                type="button"
                disabled={disabled}
                onClick={() => remove(idx)}
                className="text-rose-400 hover:text-rose-300 text-[10px] justify-self-end disabled:opacity-40"
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
