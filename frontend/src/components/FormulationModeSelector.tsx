import { useEffect, useState } from "react";
import { api } from "../api";

interface ModeChoice {
  value: string;
  label: string;
  desc: string;
}

export default function FormulationModeSelector() {
  const [current, setCurrent] = useState("hybrid");
  const [choices, setChoices] = useState<ModeChoice[]>([]);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    api.getFormulationMode().then((r) => {
      setCurrent(r.current);
      setChoices(r.choices);
    }).catch(() => {});
  }, []);

  const handleChange = async (mode: string) => {
    setSaving(true);
    setStatus(null);
    try {
      await api.setFormulationMode(mode);
      setCurrent(mode);
      setStatus("已更新（下次请求生效）");
    } catch {
      setStatus("更新失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border border-edge/60 rounded p-3 bg-panel/20">
      <h3 className="text-sm text-slate-200 mb-2">配方推荐模式</h3>
      <p className="text-[10px] text-slate-500 mb-3">
        控制配方推荐的生成策略：叠加模式同时使用知识库证据和 LLM 合成。
        对应环境变量 <code className="text-accent2/80">FORMUMIND_FORMULATION_MODE</code>
      </p>
      <div className="space-y-2">
        {choices.map((c) => (
          <label
            key={c.value}
            className={`flex items-start gap-3 rounded border px-3 py-2 cursor-pointer transition-colors ${
              current === c.value
                ? "border-accent/50 bg-accent/5"
                : "border-edge/60 hover:border-edge"
            }`}
          >
            <input
              type="radio"
              name="formulation_mode"
              value={c.value}
              checked={current === c.value}
              onChange={() => handleChange(c.value)}
              disabled={saving}
              className="mt-0.5"
            />
            <div>
              <span className="text-sm text-slate-200">{c.label}</span>
              <p className="text-[11px] text-slate-500 leading-relaxed">{c.desc}</p>
            </div>
          </label>
        ))}
      </div>
      {status && (
        <p className={`text-[10px] mt-2 ${status.includes("失败") ? "text-rose-400" : "text-emerald-400"}`}>
          {status}
        </p>
      )}
    </div>
  );
}
