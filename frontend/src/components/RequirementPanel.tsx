import { useStore } from "../store";
import type { ProductDomain } from "../api";

const DOMAINS: { value: ProductDomain; label: string }[] = [
  { value: "anticorrosion_coating", label: "防腐蚀涂料 · Anti-corrosion" },
  { value: "degreaser", label: "脱脂剂 · Degreaser" },
  { value: "surface_treatment", label: "表面处理剂 · Surface treatment" },
];

const SUBSTRATES = ["carbon_steel", "galvanized_steel", "aluminum", "stainless_steel", "magnesium_alloy"];

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
    <label className="block mb-4">
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

export default function RequirementPanel() {
  const { requirement, setField, setDomain, runResearch, runOptimize, busy } = useStore();
  const domain = requirement.domain;

  return (
    <aside className="glass rounded-xl p-4 flex flex-col gap-2 overflow-y-auto">
      <h2 className="text-sm uppercase tracking-widest text-accent2 mb-2">研发需求 · Requirements</h2>

      <label className="block mb-3">
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

      {domain === "anticorrosion_coating" && (
        <>
          <Slider label="耐盐雾 · Salt spray" unit="h" min={0} max={2000} step={24}
            value={requirement.salt_spray_hours} onChange={(v) => setField("salt_spray_hours", v)} />
          <Slider label="膜重 · Film weight" unit=" g/m²" min={0} max={200} step={5}
            value={requirement.film_weight_gsm} onChange={(v) => setField("film_weight_gsm", v)} />
          <Slider label="固化温度 · Cure temp" unit="°C" min={20} max={300} step={5}
            value={requirement.cure_temperature_c} onChange={(v) => setField("cure_temperature_c", v)} />
        </>
      )}

      {domain === "degreaser" && (
        <>
          <Slider label="清洗率 · Cleaning" unit="%" min={0} max={100}
            value={requirement.cleaning_efficiency} onChange={(v) => setField("cleaning_efficiency", v)} />
          <Slider label="pH 目标 · pH target" min={0} max={14} step={0.5}
            value={requirement.ph_target ?? 12} onChange={(v) => setField("ph_target", v)} />
        </>
      )}

      {domain === "surface_treatment" && (
        <Slider label="耐盐雾 · Salt spray" unit="h" min={0} max={500} step={12}
          value={requirement.salt_spray_hours} onChange={(v) => setField("salt_spray_hours", v)} />
      )}

      <Slider label="VOC 上限 · VOC limit" unit=" g/L" min={0} max={700} step={10}
        value={requirement.voc_limit_gpl} onChange={(v) => setField("voc_limit_gpl", v)} />

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
