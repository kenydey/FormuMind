import { useState } from "react";
import type { ProductDomain, Requirement } from "../api";
import { useStore } from "../store";
import {
  CONSTRAINT_CATALOG,
  constraintAppliesToDomain,
  constraintLabelForKey,
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
  disabled,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (v: number) => void;
  onRemove: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="bg-ink/60 border border-edge rounded p-2 mb-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-slate-300">{label}</span>
        <button
          onClick={onRemove}
          disabled={disabled}
          className="shrink-0 text-slate-600 hover:text-rose-400 text-xs leading-none w-4 h-4 flex items-center justify-center disabled:opacity-40"
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
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-accent disabled:opacity-50"
      />
    </div>
  );
}

const CATALOG_LABELS = new Set(
  CONSTRAINT_CATALOG.map((c) => constraintLabelForKey(c.key))
);

export default function ConstraintsEditor({
  domain,
  requirement,
  activeKeys,
  onActiveKeysChange,
  onSetValue,
  onClearValue,
  locked,
}: {
  domain: ProductDomain;
  requirement: Requirement;
  activeKeys: ConstraintKey[];
  onActiveKeysChange: (keys: ConstraintKey[]) => void;
  onSetValue: (key: ConstraintKey, value: number) => void;
  onClearValue: (key: ConstraintKey) => void;
  locked?: boolean;
}) {
  const { setCustomConstraint, removeCustomConstraint } = useStore();
  const [customName, setCustomName] = useState("");
  const [customValue, setCustomValue] = useState<number>(0);
  const [customUnit, setCustomUnit] = useState("");

  const catalogForDomain = CONSTRAINT_CATALOG.filter((c) => constraintAppliesToDomain(c, domain));
  const activeSet = new Set(activeKeys);
  const availableToAdd = catalogForDomain.filter((c) => !activeSet.has(c.key));

  const customEntries = Object.entries(requirement.constraints ?? {}).filter(
    ([name]) => !CATALOG_LABELS.has(name)
  );

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

  function addCustomConstraint() {
    const name = customName.trim();
    if (!name || locked) return;
    const label = customUnit ? `${name} (${customUnit})` : name;
    setCustomConstraint(label, customValue);
    setCustomName("");
    setCustomValue(0);
    setCustomUnit("");
  }

  return (
    <div className="mb-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-400 uppercase tracking-wider">
          工艺约束 · Constraints
        </span>
        <button
          onClick={resetDefaults}
          disabled={locked}
          className="text-[10px] text-slate-500 hover:text-accent transition-colors px-1.5 py-0.5 rounded border border-edge hover:border-accent/40 disabled:opacity-40"
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
            disabled={locked}
          />
        );
      })}

      {customEntries.map(([name, value]) => (
        <div key={name} className="bg-ink/60 border border-edge rounded p-2 mb-2 flex items-center justify-between gap-2">
          <div className="min-w-0">
            <span className="text-xs text-slate-300 block truncate">{name}</span>
            <span className="text-[10px] font-mono text-accent">{value}</span>
          </div>
          <button
            type="button"
            disabled={locked}
            onClick={() => removeCustomConstraint(name)}
            className="text-rose-400 text-xs disabled:opacity-40"
          >
            ×
          </button>
        </div>
      ))}

      {activeKeys.length === 0 && customEntries.length === 0 && (
        <p className="text-[11px] text-slate-500 mb-2">暂无约束。可从下方添加。</p>
      )}

      {availableToAdd.length > 0 && (
        <select
          value=""
          disabled={locked}
          onChange={(e) => {
            if (e.target.value) add(e.target.value as ConstraintKey);
          }}
          className="w-full bg-ink border border-edge rounded px-2 py-1 text-xs text-slate-400 disabled:opacity-50"
        >
          <option value="">+ 添加预设约束…</option>
          {availableToAdd.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
      )}

      <div className="flex gap-2 mt-2">
        <input
          placeholder="约束名称"
          value={customName}
          disabled={locked}
          onChange={(e) => setCustomName(e.target.value)}
          className="flex-1 bg-ink border border-edge rounded px-2 py-1 text-xs disabled:opacity-50"
        />
        <input
          type="number"
          placeholder="值"
          value={customValue}
          disabled={locked}
          onChange={(e) => setCustomValue(Number(e.target.value))}
          className="w-20 bg-ink border border-edge rounded px-2 py-1 text-xs disabled:opacity-50"
        />
        <input
          placeholder="单位"
          value={customUnit}
          disabled={locked}
          onChange={(e) => setCustomUnit(e.target.value)}
          className="w-16 bg-ink border border-edge rounded px-2 py-1 text-xs disabled:opacity-50"
        />
        <button
          type="button"
          disabled={locked || !customName.trim()}
          onClick={addCustomConstraint}
          className="text-accent text-xs border border-accent/30 rounded px-2 disabled:opacity-40"
        >
          +
        </button>
      </div>
    </div>
  );
}
