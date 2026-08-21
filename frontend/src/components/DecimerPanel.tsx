import { useEffect, useState } from "react";
import { api, type DecimerStatus } from "../api";

interface ModeChoice {
  value: string;
  label: string;
  desc: string;
}

export default function DecimerPanel() {
  const [current, setCurrent] = useState("auto");
  const [choices, setChoices] = useState<ModeChoice[]>([]);
  const [status, setStatus] = useState<DecimerStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api
      .getDecimer()
      .then((r) => {
        setCurrent(r.current);
        setChoices(r.choices);
        setStatus(r.status);
      })
      .catch(() => {});
  }, []);

  const handleChange = async (mode: string) => {
    setSaving(true);
    setMsg(null);
    try {
      await api.setDecimerMode(mode);
      setCurrent(mode);
      setMsg("已更新（下次请求生效）");
    } catch {
      setMsg("更新失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border border-edge/60 rounded p-3 bg-panel/20">
      <h3 className="text-sm text-slate-200 mb-2">DECIMER 离线结构识别</h3>
      <p className="text-[10px] text-slate-500 mb-3">
        化学结构图优先离线识别为 SMILES（免 token，省费用），失败回退视觉 LLM。
        总开关在「环境变量」Tab；此处选择运行模式。
        对应环境变量 <code className="text-accent2/80">FORMUMIND_DECIMER_MODE</code>
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
            当前进程已装 DECIMER：{" "}
            <span className={status.installed_in_process ? "text-emerald-400" : "text-amber-400"}>
              {status.installed_in_process ? "是（decimer worker 内）" : "否（主进程）"}
            </span>
          </div>
          <div>结构切分 segmentation：{status.segmentation ? "启用（gpu）" : "未启用（cpu 模式）"}</div>
          <div>
            队列 <code>{status.queue}</code> · 单张超时 {status.timeout_s}s
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
              name="decimer_mode"
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
