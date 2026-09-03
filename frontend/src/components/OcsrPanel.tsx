import { useEffect, useState } from "react";
import { api, type OcsrStatus } from "../api";

export default function OcsrPanel() {
  const [status, setStatus] = useState<OcsrStatus | null>(null);

  useEffect(() => {
    api
      .getOcsr()
      .then((r) => setStatus(r.status))
      .catch(() => {});
  }, []);

  return (
    <div className="border border-edge/60 rounded p-3 bg-panel/20">
      <h3 className="text-sm text-slate-200 mb-2">OCSR 离线结构识别</h3>
      <p className="text-[10px] text-slate-500 mb-3">
        化学结构图优先离线识别为 SMILES（免 token，省费用），失败回退视觉 LLM。
        MolScribe（torch-cpu）跑在独立 worker，不占主服务内存。
        总开关在「环境变量」Tab（
        <code className="text-accent2/80">FORMUMIND_OCSR_ENABLED</code>）。
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
            MolScribe 已装：{" "}
            <span className={status.molscribe_installed ? "text-emerald-400" : "text-amber-400"}>
              {status.molscribe_installed ? "是（molscribe worker 内）" : "否（主进程）"}
            </span>
          </div>
          <div>
            队列 <code>{status.molscribe_queue}</code> · 超时{" "}
            <code>{status.molscribe_timeout_s}s</code>
          </div>
        </div>
      )}
    </div>
  );
}
