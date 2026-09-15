/** Dual-pane shell for control strip (left) + live artifact payload (right). */

export default function ArtifactSplitLayout({
  left,
  right,
  leftLabel = "控制 · Controls",
  rightLabel = "产物 · Artifact",
  className = "",
}: {
  left: React.ReactNode;
  right: React.ReactNode;
  leftLabel?: string;
  rightLabel?: string;
  className?: string;
}) {
  return (
    <div
      className={`grid grid-cols-1 md:grid-cols-2 gap-3 min-h-0 ${className}`}
      data-testid="artifact-split-layout"
    >
      <div className="min-h-0 flex flex-col gap-2 border border-edge/50 rounded-lg p-3 bg-ink/30">
        <div className="text-[10px] uppercase tracking-widest text-slate-500 shrink-0">
          {leftLabel}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto space-y-3">{left}</div>
      </div>
      <div
        className="min-h-0 flex flex-col gap-2 border border-accent/20 rounded-lg p-3 bg-accent/5"
        data-testid="artifact-split-payload"
      >
        <div className="text-[10px] uppercase tracking-widest text-accent2 shrink-0">
          {rightLabel}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{right}</div>
      </div>
    </div>
  );
}
