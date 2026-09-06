import { useEffect, useState } from "react";
import { api } from "../api";

type Prewarm = {
  status: string;
  backend: string | null;
  elapsed_ms: number | null;
  error: string | null;
};

/**
 * Sources-column RAG prewarm control.
 * Polls status; when idle/failed shows a button that calls `prewarmRag`.
 */
export default function RagPrewarmBar() {
  const [pw, setPw] = useState<Prewarm | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const s = await api.getRagStatus();
        if (!cancelled) setPw(s.prewarm as Prewarm);
      } catch {
        /* status unavailable — skip */
      }
    };
    void poll();
    const id = window.setInterval(poll, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  async function trigger() {
    setBusy(true);
    try {
      const res = await api.prewarmRag(true);
      setPw(res);
    } catch (e) {
      setPw({
        status: "failed",
        backend: null,
        elapsed_ms: null,
        error: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(false);
    }
  }

  if (!pw) return null;

  if (pw.status === "ready") {
    return (
      <div
        className="text-[10px] text-emerald-300/90 border border-emerald-500/30 bg-emerald-500/10 rounded px-1.5 py-0.5 flex items-center justify-between gap-2"
        data-testid="rag-prewarm-ready"
      >
        <span>✓ RAG 已就绪 {pw.backend ? `· ${pw.backend}` : ""}</span>
        {pw.elapsed_ms != null && (
          <span className="font-mono text-emerald-200/70">{pw.elapsed_ms}ms</span>
        )}
      </div>
    );
  }

  if (pw.status === "failed" || pw.status === "idle") {
    return (
      <div
        className={`text-[10px] rounded px-1.5 py-0.5 flex items-center justify-between gap-2 border ${
          pw.status === "failed"
            ? "text-rose-300/90 border-rose-500/30 bg-rose-500/10"
            : "text-slate-400 border-edge/40 bg-ink/40"
        }`}
        data-testid="rag-prewarm-bar"
      >
        <span>
          {pw.status === "failed"
            ? `RAG 预热失败：${pw.error || "unknown"}`
            : "RAG 尚未预热（首次检索会更慢）"}
        </span>
        <button
          type="button"
          disabled={busy}
          onClick={() => void trigger()}
          className="shrink-0 border border-accent/40 text-accent rounded px-1.5 py-0.5 hover:bg-accent/10 disabled:opacity-40"
          data-testid="rag-prewarm-btn"
        >
          {busy ? "启动中…" : "预热 RAG"}
        </button>
      </div>
    );
  }

  return (
    <div
      className="text-[11px] text-slate-400 border border-edge/40 bg-ink/40 rounded px-2 py-1 flex items-center justify-between gap-2"
      data-testid="rag-prewarm-bar"
    >
      <span>🔥 RAG 预热中… {pw.backend || ""}</span>
      <span className="font-mono text-accent2">
        {pw.elapsed_ms != null ? `${pw.elapsed_ms}ms` : ""} · {pw.status}
      </span>
    </div>
  );
}
