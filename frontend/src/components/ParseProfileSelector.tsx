import { useEffect, useState } from "react";
import { api } from "../api";

type Availability = {
  gpu_available: boolean;
  mineru_key_present: boolean;
  vision_available: boolean;
  active_rag_backend: string;
};

const PROFILES: Record<
  string,
  { title: string; desc: string; needs: string[] }
> = {
  low: {
    title: "低配 · 纯 CPU",
    desc: "Hybrid 本地解析(云 MinerU 关)+ BM25+FAISS 检索。零 GPU/云依赖,任何 VPS 可用。",
    needs: [],
  },
  mid: {
    title: "中配 · CPU + 可选云",
    desc: "低配之上启用云 MinerU 页升级(表格/公式保真)+ GPU 检索自动探测(无 CUDA 自动回落)。",
    needs: ["MinerU Token(云解析)"],
  },
  high: {
    title: "高配 · GPU 主机",
    desc: "本地 MinerU(magic-pdf,数据不出域)+ 版面解析内置 OCR + GPU ColBERT(PyLate)。",
    needs: ["CUDA GPU ≥ 4GB"],
  },
};

export default function ParseProfileSelector({ reloadKey }: { reloadKey?: number }) {
  const [current, setCurrent] = useState<string>("");
  const [avail, setAvail] = useState<Availability | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = async () => {
    try {
      const res = await api.getParseProfile();
      setCurrent(res.profile);
      setAvail(res.availability);
    } catch {
      setMsg({ ok: false, text: "无法读取解析/检索档位配置。" });
    }
  };

  useEffect(() => {
    load();
  }, [reloadKey]);

  const apply = async (p: string) => {
    setBusy(p);
    setMsg(null);
    try {
      const res = await api.postParseProfile(p);
      setCurrent(res.profile);
      setAvail(res.availability);
      setMsg({
        ok: true,
        text: `已应用${PROFILES[p].title}${res.persisted ? "" : "(仅本次进程生效,环境文件只读)"}。切换后需重启后端使检索后端完全生效。`,
      });
    } catch {
      setMsg({ ok: false, text: "应用档位失败,请检查后端日志。" });
    } finally {
      setBusy(null);
    }
  };

  const cap = (key: keyof Availability) => avail && avail[key];

  return (
    <div className="rounded-xl border border-slate-700/60 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-slate-200">解析 / 检索档位</h3>
        {avail && (
          <span className="text-xs text-slate-500">
            当前: <b className="text-slate-300">{PROFILES[current]?.title ?? current}</b>
            {avail.active_rag_backend && ` · 检索后端 ${avail.active_rag_backend}`}
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {Object.entries(PROFILES).map(([key, p]) => {
          const missing = p.needs.filter((n) =>
            n.includes("GPU")
              ? !cap("gpu_available")
              : n.includes("MinerU")
                ? !cap("mineru_key_present")
                : false,
          );
          const disabled = busy !== null;
          return (
            <button
              key={key}
              disabled={disabled}
              onClick={() => apply(key)}
              className={`rounded-lg border p-3 text-left transition-colors ${
                current === key
                  ? "border-sky-500/70 bg-sky-500/10"
                  : "border-slate-700/70 bg-slate-800/40 hover:border-slate-500"
              } ${disabled ? "opacity-50" : ""}`}
            >
              <div className="text-sm font-medium text-slate-200">{p.title}</div>
              <div className="mt-1 text-xs text-slate-400 leading-relaxed">{p.desc}</div>
              <div className="mt-2 flex flex-wrap gap-1">
                {missing.length > 0 ? (
                  missing.map((m) => (
                    <span
                      key={m}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400"
                    >
                      缺 {m} — 将降级
                    </span>
                  ))
                ) : (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">
                    条件满足
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>

      {msg && (
        <div
          className={`mt-3 text-xs rounded px-3 py-2 border ${
            msg.ok
              ? "border-emerald-500/40 text-emerald-400 bg-emerald-500/10"
              : "border-rose-500/40 text-rose-400 bg-rose-500/10"
          }`}
        >
          {msg.text}
        </div>
      )}
      {avail && !avail.vision_available && (
        <div className="mt-2 text-[11px] text-slate-500">
          提示: 未配置视觉模型 — 扫描件中的图片型表格将只走 OCR 文本。
        </div>
      )}
    </div>
  );
}
