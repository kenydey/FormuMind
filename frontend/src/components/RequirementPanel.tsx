import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore, DOMAIN_OBJECTIVES } from "../store";
import type { ObjectiveSpec, ProductDomain, Requirement } from "../api";
import {
  normalizeObjective,
} from "../utils/objectiveContract";
import ConstraintsEditor from "./ConstraintsEditor";
import LeversEditor from "./LeversEditor";

function IntentParser() {
  const { applyIntent, intentBusy } = useStore(
    useShallow((s) => ({ applyIntent: s.applyIntent, intentBusy: s.intentBusy }))
  );
  const [text, setText] = useState("");
  const [filled, setFilled] = useState<string[] | null>(null);

  const run = async () => {
    if (!text.trim()) return;
    const fields = await applyIntent(text.trim());
    setFilled(fields);
  };

  return (
    <div className="mb-3 border border-edge rounded p-2 bg-ink/40">
      <span className="text-xs text-slate-400 uppercase tracking-wider">✨ 智能解析 · NL Intent</span>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        placeholder="用一句话描述研发项目，例如：开发汽车底盘环保水性环氧防腐涂料，耐盐雾1000小时，120℃固化"
        className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-xs text-slate-200 resize-none"
      />
      <div className="flex items-center justify-between mt-1">
        <span className="text-[10px] text-slate-500">
          {filled && filled.length > 0 ? `已填充：${filled.join(", ")}` : "解析后自动填充下方表单"}
        </span>
        <button
          onClick={run}
          disabled={intentBusy || !text.trim()}
          className="text-[11px] border border-accent text-accent rounded px-2 py-0.5 hover:bg-accent/10 disabled:opacity-40"
        >
          {intentBusy ? "解析中…" : "解析填表"}
        </button>
      </div>
    </div>
  );
}

const DOMAINS: { value: ProductDomain; label: string }[] = [
  { value: "anticorrosion_coating", label: "防腐蚀涂料 · Anti-corrosion" },
  { value: "degreaser", label: "脱脂剂 · Degreaser" },
  { value: "surface_treatment", label: "表面处理剂 · Surface treatment" },
];

const SUBSTRATES = ["carbon_steel", "galvanized_steel", "aluminum", "stainless_steel", "magnesium_alloy"];

const ALL_METRICS: { metric: string; label: string; defaultDir: "maximize" | "minimize" }[] = [
  { metric: "salt_spray_hours", label: "耐盐雾 Salt Spray (h)", defaultDir: "maximize" },
  { metric: "cleaning_efficiency", label: "清洗率 Cleaning (%)", defaultDir: "maximize" },
  { metric: "cost_cny_per_kg", label: "成本 Cost (CNY/kg)", defaultDir: "minimize" },
  { metric: "voc_gpl", label: "VOC (g/L)", defaultDir: "minimize" },
  { metric: "sustainability_idx", label: "可持续性 Sustainability", defaultDir: "maximize" },
  { metric: "coating_weight_gsm", label: "膜重 Coating Weight (g/m²)", defaultDir: "maximize" },
  { metric: "film_weight_gsm", label: "干膜重 Dry Film (g/m²)", defaultDir: "maximize" },
  { metric: "ph_value", label: "pH 值", defaultDir: "maximize" },
];

function metaFor(metric: string) {
  return ALL_METRICS.find((m) => m.metric === metric);
}

const METRIC_TO_REQUIREMENT_FIELD: Partial<Record<string, keyof Requirement>> = {
  salt_spray_hours: "salt_spray_hours",
  film_weight_gsm: "film_weight_gsm",
  coating_weight_gsm: "film_weight_gsm",
  cleaning_efficiency: "cleaning_efficiency",
};

const TARGET_BOUNDS: Record<string, { min: number; max: number; step: number; unit: string }> = {
  salt_spray_hours: { min: 0, max: 3000, step: 50, unit: " h" },
  cleaning_efficiency: { min: 0, max: 100, step: 1, unit: " %" },
  cost_cny_per_kg: { min: 0, max: 200, step: 1, unit: " CNY/kg" },
  voc_gpl: { min: 0, max: 700, step: 10, unit: " g/L" },
  sustainability_idx: { min: 0, max: 1, step: 0.05, unit: "" },
  coating_weight_gsm: { min: 0, max: 200, step: 5, unit: " g/m²" },
  film_weight_gsm: { min: 0, max: 200, step: 5, unit: " g/m²" },
  ph_value: { min: 0, max: 14, step: 0.5, unit: "" },
};

function DirectionBadge({
  direction,
  onToggle,
}: {
  direction: ObjectiveSpec["direction"];
  onToggle: () => void;
}) {
  const label =
    direction === "match_target" ? "◎" : direction === "maximize" ? "↑" : "↓";
  const title =
    direction === "match_target"
      ? "Match target (click to maximize)"
      : direction === "maximize"
        ? "Maximize (click to minimize)"
        : "Minimize (click to match target)";
  const tone =
    direction === "match_target"
      ? "bg-sky-500/20 text-sky-400 hover:bg-sky-500/30"
      : direction === "maximize"
        ? "bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30"
        : "bg-rose-500/20 text-rose-400 hover:bg-rose-500/30";
  return (
    <button
      onClick={onToggle}
      title={title}
      className={`shrink-0 w-6 h-6 rounded text-xs font-bold flex items-center justify-center transition-colors ${tone}`}
    >
      {label}
    </button>
  );
}

function ObjectivesEditor({
  objectives,
  domain,
  requirement,
  onSyncField,
  updateObjective,
  removeObjective,
  addObjective,
  resetObjectivesForDomain,
  locked,
}: {
  objectives: ObjectiveSpec[];
  domain: ProductDomain;
  requirement: Requirement;
  onSyncField: <K extends keyof Requirement>(key: K, value: Requirement[K]) => void;
  updateObjective: (idx: number, patch: Partial<ObjectiveSpec>) => void;
  removeObjective: (idx: number) => void;
  addObjective: (objective: ObjectiveSpec) => void;
  resetObjectivesForDomain: (domain: ProductDomain) => void;
  locked?: boolean;
}) {
  const usedMetrics = new Set(objectives.map((o) => o.metric));
  const availableToAdd = ALL_METRICS.filter((m) => !usedMetrics.has(m.metric));

  const totalWeight = objectives.reduce((s, o) => s + o.weight, 0);
  const weightOk = Math.abs(totalWeight - 1.0) < 0.01;

  function seedTarget(metric: string): number | null {
    const field = METRIC_TO_REQUIREMENT_FIELD[metric];
    if (field) {
      const v = requirement[field];
      return typeof v === "number" ? v : null;
    }
    return null;
  }

  function update(idx: number, patch: Partial<ObjectiveSpec>) {
    updateObjective(idx, patch);
    if (patch.target_value != null) {
      const metric = objectives[idx]?.metric;
      const field = metric ? METRIC_TO_REQUIREMENT_FIELD[metric] : undefined;
      if (field) onSyncField(field, patch.target_value as Requirement[typeof field]);
    }
  }

  function remove(idx: number) {
    removeObjective(idx);
  }

  function addMetric(metric: string) {
    const meta = metaFor(metric);
    addObjective(
      normalizeObjective({
        metric,
        weight: 0.1,
        direction: meta?.defaultDir ?? "maximize",
        target_value: seedTarget(metric),
      })
    );
  }

  function resetDefaults() {
    resetObjectivesForDomain(domain);
    for (const o of DOMAIN_OBJECTIVES[domain]) {
      const field = METRIC_TO_REQUIREMENT_FIELD[o.metric];
      const tv = seedTarget(o.metric);
      if (field && tv != null) onSyncField(field, tv as Requirement[typeof field]);
    }
  }

  function addCustomObjective() {
    addObjective(
      normalizeObjective({
        metric: `custom_${Date.now().toString(36)}`,
        display_name: "自定义指标",
        weight: 0.1,
        direction: "maximize",
        target_value: null,
        unit: "",
        ref_min: null,
        ref_max: null,
      })
    );
  }

  return (
    <div className="mb-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-400 uppercase tracking-wider">优化目标 · Objectives</span>
        <button
          onClick={resetDefaults}
          disabled={locked}
          className="text-[10px] text-slate-500 hover:text-accent transition-colors px-1.5 py-0.5 rounded border border-edge hover:border-accent/40 disabled:opacity-40"
        >
          重置默认
        </button>
      </div>

      <div className="flex flex-col gap-2">
        {objectives.map((obj, idx) => {
          const meta = metaFor(obj.metric);
          const bounds = TARGET_BOUNDS[obj.metric];
          return (
            <div key={`${obj.metric}-${idx}`} className="bg-ink/60 border border-edge rounded p-2 space-y-1.5">
              <div className="flex items-center gap-2">
                <DirectionBadge
                  direction={obj.direction}
                  onToggle={() =>
                    !locked &&
                    update(idx, {
                      direction:
                        obj.direction === "maximize"
                          ? "minimize"
                          : obj.direction === "minimize"
                            ? "match_target"
                            : "maximize",
                    })
                  }
                />
                <input
                  value={obj.display_name ?? ""}
                  disabled={locked}
                  onChange={(e) => update(idx, { display_name: e.target.value })}
                  className="flex-1 bg-ink border border-edge rounded px-2 py-0.5 text-xs text-slate-200 disabled:opacity-50"
                  placeholder="显示名称"
                />
                <button
                  onClick={() => !locked && remove(idx)}
                  disabled={locked}
                  className="shrink-0 text-slate-600 hover:text-rose-400 text-xs leading-none w-4 h-4 flex items-center justify-center disabled:opacity-40"
                  title="Remove objective"
                >
                  ×
                </button>
              </div>
              <div className="flex items-center gap-2">
                <input
                  value={obj.metric}
                  disabled={locked}
                  onChange={(e) => update(idx, { metric: e.target.value.trim() })}
                  className="flex-1 bg-ink border border-edge rounded px-2 py-0.5 text-xs font-mono text-slate-400 disabled:opacity-50"
                  placeholder="metric id"
                />
                <span className="text-[10px] text-slate-500 truncate max-w-[80px]">{meta?.label ?? ""}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-slate-500 shrink-0 w-10">权重</span>
                <input
                  type="range"
                  min={0.05}
                  max={1}
                  step={0.05}
                  value={obj.weight}
                  disabled={locked}
                  onChange={(e) => update(idx, { weight: Number(e.target.value) })}
                  className="flex-1 accent-accent disabled:opacity-50"
                />
                <span className="text-[10px] font-mono text-accent w-8 text-right shrink-0">
                  {obj.weight.toFixed(2)}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <label className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-slate-500">目标值</span>
                  <input
                    type="number"
                    disabled={locked}
                    value={obj.target_value ?? seedTarget(obj.metric) ?? ""}
                    onChange={(e) => {
                      const v = e.target.value === "" ? null : Number(e.target.value);
                      update(idx, { target_value: v });
                    }}
                    className="bg-ink border border-edge rounded px-2 py-0.5 text-xs font-mono text-slate-200 disabled:opacity-50"
                    placeholder="—"
                  />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-slate-500">单位</span>
                  <input
                    value={obj.unit ?? ""}
                    disabled={locked}
                    onChange={(e) => update(idx, { unit: e.target.value })}
                    className="bg-ink border border-edge rounded px-2 py-0.5 text-xs disabled:opacity-50"
                    placeholder={bounds?.unit ?? "单位"}
                  />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-slate-500">下界</span>
                  <input
                    type="number"
                    disabled={locked}
                    value={obj.ref_min ?? ""}
                    onChange={(e) =>
                      update(idx, { ref_min: e.target.value === "" ? null : Number(e.target.value) })
                    }
                    className="bg-ink border border-edge rounded px-2 py-0.5 text-xs font-mono disabled:opacity-50"
                  />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-slate-500">上界</span>
                  <input
                    type="number"
                    disabled={locked}
                    value={obj.ref_max ?? ""}
                    onChange={(e) =>
                      update(idx, { ref_max: e.target.value === "" ? null : Number(e.target.value) })
                    }
                    className="bg-ink border border-edge rounded px-2 py-0.5 text-xs font-mono disabled:opacity-50"
                  />
                </label>
              </div>
            </div>
          );
        })}
      </div>

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

      {availableToAdd.length > 0 && (
        <select
          value=""
          disabled={locked}
          onChange={(e) => {
            if (e.target.value) addMetric(e.target.value);
          }}
          className="mt-2 w-full bg-ink border border-edge rounded px-2 py-1 text-xs text-slate-400 disabled:opacity-50"
        >
          <option value="">+ 添加预设目标…</option>
          {availableToAdd.map((m) => (
            <option key={m.metric} value={m.metric}>
              {m.label}
            </option>
          ))}
        </select>
      )}
      <button
        type="button"
        disabled={locked}
        onClick={addCustomObjective}
        className="mt-2 w-full text-[11px] border border-accent/40 text-accent rounded px-2 py-1 hover:bg-accent/10 disabled:opacity-40"
      >
        + 自定义目标
      </button>
    </div>
  );
}

function ExampleLoader() {
  const loadExampleProject = useStore((s) => s.loadExampleProject);
  const [examples, setExamples] = useState<{ id: string; label: string }[]>([]);

  useEffect(() => {
    fetch("/api/meta")
      .then((r) => r.json())
      .then((meta) => setExamples(meta.example_projects ?? []))
      .catch(() => {});
  }, []);

  return (
    <label className="block mb-3">
      <span className="text-xs text-slate-400">加载示例项目 · Examples</span>
      <select
        defaultValue=""
        onChange={(e) => {
          if (e.target.value) void loadExampleProject(e.target.value);
        }}
        className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm"
      >
        <option value="">选择内置示例…</option>
        {examples.map((ex) => (
          <option key={ex.id} value={ex.id}>
            {ex.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function RequirementPanel({ embedded }: { embedded?: boolean }) {
  const {
    requirement,
    setField,
    setDomain,
    setLevers,
    updateObjective,
    removeObjective,
    addObjective,
    resetObjectivesForDomain,
    activeConstraints,
    setActiveConstraints,
    setConstraintValue,
    clearConstraintValue,
    addCustomConstraint,
    removeCustomConstraint,
    updateCustomConstraint,
    saveRequirementAndRefresh,
    unlockRequirement,
    resetRequirement,
    requirementLocked,
    requirementSnapshot,
    projectSaveBusy,
    runResearch,
    runOptimize,
    busy,
    formulationBusy,
  } = useStore(
    useShallow((s) => ({
      requirement: s.requirement,
      setField: s.setField,
      setDomain: s.setDomain,
      setLevers: s.setLevers,
      updateObjective: s.updateObjective,
      removeObjective: s.removeObjective,
      addObjective: s.addObjective,
      resetObjectivesForDomain: s.resetObjectivesForDomain,
      activeConstraints: s.activeConstraints,
      setActiveConstraints: s.setActiveConstraints,
      setConstraintValue: s.setConstraintValue,
      clearConstraintValue: s.clearConstraintValue,
      addCustomConstraint: s.addCustomConstraint,
      removeCustomConstraint: s.removeCustomConstraint,
      updateCustomConstraint: s.updateCustomConstraint,
      saveRequirementAndRefresh: s.saveRequirementAndRefresh,
      unlockRequirement: s.unlockRequirement,
      resetRequirement: s.resetRequirement,
      requirementLocked: s.requirementLocked,
      requirementSnapshot: s.requirementSnapshot,
      projectSaveBusy: s.projectSaveBusy,
      runResearch: s.runResearch,
      runOptimize: s.runOptimize,
      busy: s.busy,
      formulationBusy: s.formulationBusy,
    }))
  );
  const domain = requirement.domain;
  const locked = requirementLocked;

  return (
    <aside className={embedded ? "flex flex-col gap-1" : "glass rounded-xl p-4 flex flex-col gap-1 overflow-y-auto"}>
      {!embedded && (
        <h2 className="text-sm uppercase tracking-widest text-accent2 mb-2">研发需求 · Requirements</h2>
      )}

      <IntentParser />
      <ExampleLoader />

      <label className="block mb-2">
        <span className="text-xs text-slate-400">产品类型 · Product type</span>
        <input
          value={requirement.product_type ?? ""}
          disabled={locked}
          onChange={(e) => setField("product_type", e.target.value)}
          placeholder="例如：水性环氧防腐底漆"
          className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm disabled:opacity-50"
        />
      </label>

      <label className="block mb-2">
        <span className="text-xs text-slate-400">应用场景 · Application</span>
        <input
          value={requirement.application ?? requirement.substrate}
          disabled={locked}
          onChange={(e) => setField("application", e.target.value)}
          placeholder="例如：carbon_steel / 汽车底盘"
          className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm disabled:opacity-50"
        />
      </label>

      <details className="mb-2 border border-edge/60 rounded px-2 py-1">
        <summary className="text-xs text-slate-500 cursor-pointer py-1">高级：Legacy 域预设</summary>
        <label className="block mt-2 mb-2">
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
        <label className="block mb-1">
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
      </details>

      <div className="border-t border-edge mb-3" />

      <ObjectivesEditor
        objectives={requirement.objectives}
        domain={domain}
        requirement={requirement}
        onSyncField={setField}
        updateObjective={updateObjective}
        removeObjective={removeObjective}
        addObjective={addObjective}
        resetObjectivesForDomain={resetObjectivesForDomain}
        locked={locked}
      />

      <div className="border-t border-edge mb-3" />

      <LeversEditor levers={requirement.levers ?? []} onChange={setLevers} disabled={locked} />

      <div className="border-t border-edge mb-3" />

      <ConstraintsEditor
        domain={domain}
        requirement={requirement}
        activeKeys={activeConstraints}
        onActiveKeysChange={setActiveConstraints}
        onSetValue={setConstraintValue}
        onClearValue={clearConstraintValue}
        onAddCustom={addCustomConstraint}
        onRemoveCustom={removeCustomConstraint}
        onUpdateCustom={updateCustomConstraint}
        locked={locked}
      />

      <div className="flex gap-2 mb-3">
        <button
          type="button"
          onClick={() => void saveRequirementAndRefresh()}
          disabled={projectSaveBusy || formulationBusy || locked}
          className="bg-accent text-ink font-semibold rounded px-3 py-1.5 text-sm flex-1 disabled:opacity-40"
        >
          {projectSaveBusy || formulationBusy ? "保存中…" : "💾 保存需求"}
        </button>
        <button
          type="button"
          onClick={unlockRequirement}
          disabled={!locked}
          className="border border-edge text-slate-400 rounded px-3 py-1.5 text-sm disabled:opacity-40"
        >
          ✏️ 修改
        </button>
        <button
          type="button"
          onClick={resetRequirement}
          disabled={!requirementSnapshot || locked}
          className="border border-edge text-slate-400 rounded px-3 py-1.5 text-sm disabled:opacity-40"
        >
          恢复
        </button>
      </div>

      {!embedded && (
        <div className="mt-auto pt-3 flex flex-col gap-2">
          <button
            disabled={busy !== "idle" || formulationBusy}
            onClick={runResearch}
            className="bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-2 text-sm disabled:opacity-40"
          >
            {formulationBusy ? "检索中…" : "① 检索专利并推荐配方"}
          </button>
          <button
            disabled={busy !== "idle"}
            onClick={runOptimize}
            className="border border-accent2 text-accent2 hover:bg-accent2/10 rounded px-3 py-2 text-sm disabled:opacity-40"
          >
            {busy === "optimizing" ? "寻优中…" : "② 运行 DOE 寻优闭环"}
          </button>
        </div>
      )}
    </aside>
  );
}
