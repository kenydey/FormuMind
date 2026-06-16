import { useStore, DOMAIN_OBJECTIVES } from "../store";
import type { ObjectiveSpec, ProductDomain } from "../api";

const DOMAINS: { value: ProductDomain; label: string }[] = [
  { value: "anticorrosion_coating", label: "防腐蚀涂料 · Anti-corrosion" },
  { value: "degreaser", label: "脱脂剂 · Degreaser" },
  { value: "surface_treatment", label: "表面处理剂 · Surface treatment" },
];

const SUBSTRATES = ["carbon_steel", "galvanized_steel", "aluminum", "stainless_steel", "magnesium_alloy"];

// All possible metrics a user can choose as objectives.
const ALL_METRICS: { metric: string; label: string; defaultDir: "maximize" | "minimize" }[] = [
  { metric: "salt_spray_hours",    label: "耐盐雾 Salt Spray (h)",       defaultDir: "maximize" },
  { metric: "cleaning_efficiency", label: "清洗率 Cleaning (%)",         defaultDir: "maximize" },
  { metric: "cost_cny_per_kg",     label: "成本 Cost (CNY/kg)",          defaultDir: "minimize" },
  { metric: "voc_gpl",             label: "VOC (g/L)",                   defaultDir: "minimize" },
  { metric: "sustainability_idx",  label: "可持续性 Sustainability",      defaultDir: "maximize" },
  { metric: "coating_weight_gsm",  label: "膜重 Coating Weight (g/m²)",  defaultDir: "maximize" },
  { metric: "film_weight_gsm",     label: "干膜重 Dry Film (g/m²)",      defaultDir: "maximize" },
  { metric: "ph_value",            label: "pH 值",                       defaultDir: "maximize" },
];

function metaFor(metric: string) {
  return ALL_METRICS.find((m) => m.metric === metric);
}

function Slider(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block mb-3">
      <div className="flex justify-between text-xs text-slate-400 mb-1">
        <span>{props.label}</span>
        <span className="text-accent font-mono">
          {props.value}
          {props.unit ?? ""}
        </span>
      </div>
      <input
        type="range"
        min={props.min}
        max={props.max}
        step={props.step ?? 1}
        value={props.value}
        onChange={(e) => props.onChange(Number(e.target.value))}
        className="w-full accent-accent"
      />
    </label>
  );
}

function DirectionBadge({
  direction,
  onToggle,
}: {
  direction: "maximize" | "minimize";
  onToggle: () => void;
}) {
  const isMax = direction === "maximize";
  return (
    <button
      onClick={onToggle}
      title={isMax ? "Click to minimize" : "Click to maximize"}
      className={`shrink-0 w-6 h-6 rounded text-xs font-bold flex items-center justify-center transition-colors ${
        isMax
          ? "bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30"
          : "bg-rose-500/20 text-rose-400 hover:bg-rose-500/30"
      }`}
    >
      {isMax ? "↑" : "↓"}
    </button>
  );
}

function ObjectivesEditor({
  objectives,
  onChange,
  domain,
}: {
  objectives: ObjectiveSpec[];
  onChange: (objs: ObjectiveSpec[]) => void;
  domain: ProductDomain;
}) {
  const usedMetrics = new Set(objectives.map((o) => o.metric));
  const availableToAdd = ALL_METRICS.filter((m) => !usedMetrics.has(m.metric));

  const totalWeight = objectives.reduce((s, o) => s + o.weight, 0);
  const weightOk = Math.abs(totalWeight - 1.0) < 0.01;

  function update(idx: number, patch: Partial<ObjectiveSpec>) {
    const next = objectives.map((o, i) => (i === idx ? { ...o, ...patch } : o));
    onChange(next);
  }

  function remove(idx: number) {
    onChange(objectives.filter((_, i) => i !== idx));
  }

  function addMetric(metric: string) {
    const meta = metaFor(metric);
    onChange([
      ...objectives,
      { metric, weight: 0.1, direction: meta?.defaultDir ?? "maximize" },
    ]);
  }

  function resetDefaults() {
    onChange([...DOMAIN_OBJECTIVES[domain]]);
  }

  return (
    <div className="mb-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-400 uppercase tracking-wider">
          优化目标 · Objectives
        </span>
        <button
          onClick={resetDefaults}
          className="text-[10px] text-slate-500 hover:text-accent transition-colors px-1.5 py-0.5 rounded border border-edge hover:border-accent/40"
        >
          重置默认
        </button>
      </div>

      {/* Objective rows */}
      <div className="flex flex-col gap-2">
        {objectives.map((obj, idx) => {
          const meta = metaFor(obj.metric);
          return (
            <div key={obj.metric} className="bg-ink/60 border border-edge rounded p-2 space-y-1.5">
              <div className="flex items-center gap-2">
                <DirectionBadge
                  direction={obj.direction}
                  onToggle={() =>
                    update(idx, {
                      direction: obj.direction === "maximize" ? "minimize" : "maximize",
                    })
                  }
                />
                <span className="flex-1 text-xs text-slate-300 truncate">
                  {meta?.label ?? obj.metric}
                </span>
                <button
                  onClick={() => remove(idx)}
                  className="shrink-0 text-slate-600 hover:text-rose-400 text-xs leading-none w-4 h-4 flex items-center justify-center"
                  title="Remove objective"
                >
                  ×
                </button>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="range"
                  min={0.05}
                  max={1}
                  step={0.05}
                  value={obj.weight}
                  onChange={(e) => update(idx, { weight: Number(e.target.value) })}
                  className="flex-1 accent-accent"
                />
                <span className="text-[10px] font-mono text-accent w-8 text-right shrink-0">
                  {obj.weight.toFixed(2)}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Weight sum indicator */}
      {objectives.length > 0 && (
        <div
          className={`mt-1.5 text-[10px] text-right font-mono ${
            weightOk ? "text-slate-600" : "text-amber-400"
          }`}
        >
          Σ weights = {totalWeight.toFixed(2)}
          {!weightOk && " ⚠ (will be normalised)"}
        </div>
      )}

      {/* Add objective */}
      {availableToAdd.length > 0 && (
        <select
          value=""
          onChange={(e) => { if (e.target.value) addMetric(e.target.value); }}
          className="mt-2 w-full bg-ink border border-edge rounded px-2 py-1 text-xs text-slate-400"
        >
          <option value="">+ 添加目标 Add objective…</option>
          {availableToAdd.map((m) => (
            <option key={m.metric} value={m.metric}>
              {m.label}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

export default function RequirementPanel() {
  const { requirement, setField, setDomain, setObjectives, runResearch, runOptimize, busy } =
    useStore();
  const domain = requirement.domain;

  return (
    <aside className="glass rounded-xl p-4 flex flex-col gap-1 overflow-y-auto">
      <h2 className="text-sm uppercase tracking-widest text-accent2 mb-2">研发需求 · Requirements</h2>

      {/* Domain */}
      <label className="block mb-2">
        <span className="text-xs text-slate-400">产品域 · Domain</span>
        <select
          value={domain}
          onChange={(e) => setDomain(e.target.value as ProductDomain)}
          className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm"
        >
          {DOMAINS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </select>
      </label>

      {/* Substrate */}
      <label className="block mb-3">
        <span className="text-xs text-slate-400">基材 · Substrate</span>
        <select
          value={requirement.substrate}
          onChange={(e) => setField("substrate", e.target.value)}
          className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm"
        >
          {SUBSTRATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>

      {/* Divider */}
      <div className="border-t border-edge mb-3" />

      {/* Dynamic objectives editor */}
      <ObjectivesEditor
        objectives={requirement.objectives}
        onChange={setObjectives}
        domain={domain}
      />

      {/* Divider */}
      <div className="border-t border-edge mb-3" />

      {/* Process / constraint parameters */}
      <span className="text-xs text-slate-400 uppercase tracking-wider mb-2 block">
        工艺约束 · Constraints
      </span>

      <Slider
        label="VOC 上限 · VOC limit"
        unit=" g/L"
        min={0}
        max={700}
        step={10}
        value={requirement.voc_limit_gpl}
        onChange={(v) => setField("voc_limit_gpl", v)}
      />

      {domain === "anticorrosion_coating" && (
        <Slider
          label="固化温度上限 · Max cure temp"
          unit="°C"
          min={20}
          max={300}
          step={5}
          value={requirement.cure_temperature_c}
          onChange={(v) => setField("cure_temperature_c", v)}
        />
      )}

      {domain === "degreaser" && (
        <label className="block mb-3">
          <div className="flex justify-between text-xs text-slate-400 mb-1">
            <span>pH 目标 · pH target</span>
            <span className="text-accent font-mono">
              {requirement.ph_target ?? "—"}
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={14}
            step={0.5}
            value={requirement.ph_target ?? 12}
            onChange={(e) => setField("ph_target", Number(e.target.value))}
            className="w-full accent-accent"
          />
        </label>
      )}

      {/* Action buttons */}
      <div className="mt-auto pt-3 flex flex-col gap-2">
        <button
          disabled={busy !== "idle"}
          onClick={runResearch}
          className="bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-2 text-sm disabled:opacity-40"
        >
          {busy === "researching" ? "检索中…" : "① 检索专利并推荐配方"}
        </button>
        <button
          disabled={busy !== "idle"}
          onClick={runOptimize}
          className="border border-accent2 text-accent2 hover:bg-accent2/10 rounded px-3 py-2 text-sm disabled:opacity-40"
        >
          {busy === "optimizing" ? "寻优中…" : "② 运行 DOE 寻优闭环"}
        </button>
      </div>
    </aside>
  );
}
