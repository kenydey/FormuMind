import { useEffect, useState } from "react";
import { api, type OcsrStatus } from "../api";

interface BackendChoice {
  value: string;
  label: string;
  desc: string;
}

export default function OcsrPanel() {
  const [current, setCurrent] = useState("auto");
  const [choices, setChoices] = useState<BackendChoice[]>([]);
  const [status, setStatus] = useState<OcsrStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api
      .getOcsr()
      .then((r) => {
        setCurrent(r.current);
        setChoices(r.choices);
        setStatus(r.status);
      })
      .catch(() => {});
  }, []);

  const handleChange = async (backend: string) => {
    setSaving(true);
    setMsg(null);
    try {
      await api.setOcsrBackend(backend);
      setCurrent(backend);
      setMsg("已更新（下次请求生效）");
    } catch {
      setMsg("更新失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border border-edge/60 rounded p-3 bg-panel/20">
      <h3 className="text-sm text-slate-200 mb-2">OCSR 离线结构识别</h3>
      <p className="text-[10px] text-slate-500 mb-3">
        化学结构图优先离线识别为 SMILES（免 token，省费用），失败回退视觉 LLM。
        无 GPU → MolScribe（torch-cpu）；有 GPU → DECIMER + segmentation。
        总开关在「环境变量」Tab；此处选择识别后端。
        对应环境变量 <code className="text-accent2/80">FORMUMIND_OCSR_BACKEND</code>
      </p>

      {status && (
        <div className="text-[10px] text-slate-500 mb-3 space-y-0.5 rounded border border-edge/40 px-2 py-1.5">
          <div>
            总开关：{" "}
            <span className={status.enabled ? "text-emerald-400" : "text-slate-400"}>
              {status.enabled ? "已开启" : "未开启"}
            </span>
          </div>
          <div>
            当前后端：{" "}
            <span className="text-accent2/80">{status.backend}</span>
          </div>
          <div>
            MolScribe 已装：{" "}
            <span className={status.molscribe_installed ? "text-emerald-400" : "text-amber-400"}>
              {status.molscribe_installed ? "是（molscribe worker 内）" : "否（主进程）"}
            </span>
          </div>
          <div>
            DECIMER 已装：{" "}
            <span className={status.decimer_installed ? "text-emerald-400" : "text-amber-400"}>
              {status.decimer_installed ? "是（decimer worker 内）" : "否（主进程）"}
            </span>
          </div>
          <div>
            队列 molscribe <code>{status.molscribe_queue}</code> · decimer{" "}
            <code>{status.decimer_queue}</code>
          </div>
        </div>
      )}

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
              name="ocsr_backend"
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
      {msg && (
        <p className={`text-[10px] mt-2 ${msg.includes("失败") ? "text-rose-400" : "text-emerald-400"}`}>
          {msg}
        </p>
      )}
    </div>
  );
}
