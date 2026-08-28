import { useEffect, useState } from "react";
import { api } from "../api";

type Prewarm = { status: string; backend: string | null; elapsed_ms: number | null; error: string | null };

export default function RagPrewarmBar() {
  const [pw, setPw] = useState<Prewarm | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const s = await api.getRagStatus();
        if (!cancelled) setPw(s.prewarm as Prewarm);
      } catch {}
    };
    poll();
    const id = window.setInterval(poll, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  if (!pw || pw.status === "ready" || pw.status === "idle") return null;
  if (pw.status === "failed") {
    return <div className="text-[10px] text-rose-300/90 border border-rose-500/30 bg-rose-500/10 rounded px-1.5 py-0.5">RAG 预热失败：{pw.error || "unknown"}</div>;
  }
  return (
    <div className="text-[11px] text-slate-400 border border-edge/40 bg-ink/40 rounded px-2 py-1 flex items-center justify-between gap-2">
      <span>🔥 RAG 预热中… {pw.backend || ""}</span>
      <span className="font-mono text-accent2">{pw.elapsed_ms != null ? `${pw.elapsed_ms}ms` : ""} · {pw.status}</span>
    </div>
  );
}
