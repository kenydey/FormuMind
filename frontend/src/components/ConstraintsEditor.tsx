import type { ProductDomain, Requirement } from "../api";
import {
  CONSTRAINT_CATALOG,
  constraintAppliesToDomain,
  defaultConstraintsForDomain,
  getConstraintValue,
  type ConstraintKey,
} from "../constants/constraints";

function ConstraintSlider({
  label,
  value,
  min,
  max,
  step,
  unit,
  onChange,
  onRemove,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (v: number) => void;
  onRemove: () => void;
}) {
  return (
    <div className="bg-ink/60 border border-edge rounded p-2 mb-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-slate-300">{label}</span>
        <button
          onClick={onRemove}
          className="shrink-0 text-slate-600 hover:text-rose-400 text-xs leading-none w-4 h-4 flex items-center justify-center"
          title="Remove constraint"
        >
          ×
        </button>
      </div>
      <div className="flex justify-between text-[10px] text-slate-500 mb-1">
        <span>数值</span>
        <span className="text-accent font-mono">
          {value}
          {unit}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-accent"
      />
    </div>
  );
}

export default function ConstraintsEditor({
  domain,
  requirement,
  activeKeys,
  onActiveKeysChange,
  onSetValue,
  onClearValue,
}: {
  domain: ProductDomain;
  requirement: Requirement;
  activeKeys: ConstraintKey[];
  onActiveKeysChange: (keys: ConstraintKey[]) => void;
  onSetValue: (key: ConstraintKey, value: number) => void;
  onClearValue: (key: ConstraintKey) => void;
}) {
  const catalogForDomain = CONSTRAINT_CATALOG.filter((c) => constraintAppliesToDomain(c, domain));
  const activeSet = new Set(activeKeys);
  const availableToAdd = catalogForDomain.filter((c) => !activeSet.has(c.key));

  function remove(key: ConstraintKey) {
    onActiveKeysChange(activeKeys.filter((k) => k !== key));
    onClearValue(key);
  }

  function add(key: ConstraintKey) {
    onActiveKeysChange([...activeKeys, key]);
    const def = CONSTRAINT_CATALOG.find((c) => c.key === key);
    if (def) onSetValue(key, getConstraintValue(requirement, key));
  }

  function resetDefaults() {
    const defaults = defaultConstraintsForDomain(domain);
    onActiveKeysChange(defaults);
    for (const def of CONSTRAINT_CATALOG) {
      if (!constraintAppliesToDomain(def, domain)) onClearValue(def.key);
    }
    for (const key of defaults) {
      onSetValue(key, getConstraintValue(requirement, key));
    }
  }

  return (
    <div className="mb-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-400 uppercase tracking-wider">
          工艺约束 · Constraints
        </span>
        <button
          onClick={resetDefaults}
          className="text-[10px] text-slate-500 hover:text-accent transition-colors px-1.5 py-0.5 rounded border border-edge hover:border-accent/40"
        >
          重置默认
        </button>
      </div>

      {activeKeys.map((key) => {
        const def = CONSTRAINT_CATALOG.find((c) => c.key === key);
        if (!def || !constraintAppliesToDomain(def, domain)) return null;
        return (
          <ConstraintSlider
            key={key}
            label={def.label}
            value={getConstraintValue(requirement, key)}
            min={def.min}
            max={def.max}
            step={def.step}
            unit={def.unit}
            onChange={(v) => onSetValue(key, v)}
            onRemove={() => remove(key)}
          />
        );
      })}

      {activeKeys.length === 0 && (
        <p className="text-[11px] text-slate-500 mb-2">暂无约束。可从下方添加。</p>
      )}

      {availableToAdd.length > 0 && (
        <select
          value=""
          onChange={(e) => {
            if (e.target.value) add(e.target.value as ConstraintKey);
          }}
          className="w-full bg-ink border border-edge rounded px-2 py-1 text-xs text-slate-400"
        >
          <option value="">+ 添加约束 Add constraint…</option>
          {availableToAdd.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
