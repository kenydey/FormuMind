import type { ThinkingStep } from "../api";

function statusGlyph(status: ThinkingStep["status"]): string {
  switch (status) {
    case "done":
      return "✓";
    case "error":
      return "!";
    case "pending":
      return "·";
    default:
      return "›";
  }
}

function statusClass(status: ThinkingStep["status"]): string {
  switch (status) {
    case "done":
      return "text-emerald-400/90 border-emerald-500/30";
    case "error":
      return "text-rose-300 border-rose-500/40";
    case "running":
      return "text-accent border-accent/40";
    default:
      return "text-slate-500 border-edge/50";
  }
}

/** Collapsible thinking / tool timeline for long Celery tasks (Dim-2). */
export default function ThinkingTimeline({
  steps,
  title = "思考链路",
  compact = false,
}: {
  steps: ThinkingStep[];
  title?: string;
  compact?: boolean;
}) {
  if (!steps.length) return null;

  return (
    <div
      className={`rounded-lg border border-edge/60 bg-ink/40 ${compact ? "px-2 py-1.5" : "px-3 py-2"}`}
      data-testid="thinking-timeline"
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[10px] uppercase tracking-widest text-slate-500">{title}</span>
        <span className="text-[10px] text-slate-600">{steps.length} 步</span>
      </div>
      <ol className="space-y-1 max-h-40 overflow-y-auto">
        {steps.map((s) => (
          <li
            key={s.id}
            className={`flex gap-2 items-start text-[11px] border-l-2 pl-2 ${statusClass(s.status)}`}
            data-testid={`thinking-step-${s.id}`}
            data-status={s.status || "running"}
          >
            <span className="font-mono shrink-0 w-3 text-center mt-0.5">{statusGlyph(s.status)}</span>
            <div className="min-w-0 flex-1">
              <div
                className={`truncate ${
                  s.status === "running" ? "font-semibold animate-pulse" : ""
                }`}
              >
                {s.title}
              </div>
              {s.detail && s.detail !== s.title && (
                <div className="text-[10px] text-slate-500 truncate">{s.detail}</div>
              )}
            </div>
            {s.kind && s.kind !== "stage" && (
              <span className="text-[9px] text-slate-600 shrink-0 uppercase">{s.kind}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
