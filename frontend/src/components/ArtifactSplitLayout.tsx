/** Stacked shell: controls on top, live artifact payload below (full width).

Introduced with the artifact workspace as a dual-pane (left controls / right
payload). Side-by-side made recommend / DOE / optimize modals feel cramped —
the shell now stacks vertically so the leaderboard and DOE matrix sit under
the action buttons, matching the pre-artifact-workspace layout while keeping
the control / payload slots and "open in workspace" entry points.
*/

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
      className={`flex flex-col gap-3 min-h-0 ${className}`}
      data-testid="artifact-split-layout"
      data-layout="stack"
    >
      <div className="shrink-0 flex flex-col gap-2 border border-edge/50 rounded-lg p-3 bg-ink/30">
        <div className="text-[10px] uppercase tracking-widest text-slate-500 shrink-0">
          {leftLabel}
        </div>
        <div className="space-y-3">{left}</div>
      </div>
      <div
        className="min-h-0 flex flex-col gap-2 border border-accent/20 rounded-lg p-3 bg-accent/5"
        data-testid="artifact-split-payload"
      >
        <div className="text-[10px] uppercase tracking-widest text-accent2 shrink-0">
          {rightLabel}
        </div>
        <div className="min-h-0 overflow-y-auto">{right}</div>
      </div>
    </div>
  );
}
