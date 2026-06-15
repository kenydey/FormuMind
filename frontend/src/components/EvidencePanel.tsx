import { useState } from "react";
import { useStore } from "../store";
import type { Evidence } from "../api";

function EvidenceCard({ ev }: { ev: Evidence }) {
  const [expanded, setExpanded] = useState(false);
  const relevancePct = Math.round(ev.relevance * 100);
  return (
    <div className="border border-edge/60 rounded-lg p-2.5 bg-ink/50 text-[11px]">
      <div className="flex items-start gap-2 justify-between">
        <div className="flex-1 min-w-0">
          <span className="text-accent2 font-mono mr-1.5">[{ev.identifier}]</span>
          <span className="text-slate-400 mr-1.5 uppercase text-[10px]">{ev.source}</span>
          <span className="text-slate-200">{ev.title}</span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <div className="w-10 h-1 bg-edge rounded overflow-hidden">
            <div className="h-full bg-accent2" style={{ width: `${relevancePct}%` }} />
          </div>
          <span className="text-slate-500 font-mono">{relevancePct}%</span>
          <button
            onClick={() => setExpanded((e) => !e)}
            className="text-slate-500 hover:text-accent ml-1"
            title={expanded ? "收起" : "展开摘录"}
          >
            {expanded ? "▲" : "▼"}
          </button>
        </div>
      </div>
      {expanded && (
        <p className="mt-1.5 text-slate-400 leading-relaxed border-t border-edge/40 pt-1.5">
          {ev.snippet}
        </p>
      )}
    </div>
  );
}

export default function EvidencePanel() {
  const research = useStore((s) => s.research);
  if (!research) return null;

  const sorted = [...research.evidence].sort((a, b) => b.relevance - a.relevance);

  return (
    <div className="space-y-2">
      {research.mechanism && (
        <div className="bg-accent2/10 border border-accent2/30 rounded-lg p-2.5 text-[11px] text-slate-300 leading-relaxed">
          <span className="text-accent2 font-semibold text-[10px] uppercase tracking-widest mr-2">机理</span>
          {research.mechanism}
        </div>
      )}
      <div className="space-y-1.5 max-h-36 overflow-y-auto pr-0.5">
        {sorted.map((ev) => (
          <EvidenceCard key={ev.identifier} ev={ev} />
        ))}
      </div>
    </div>
  );
}
