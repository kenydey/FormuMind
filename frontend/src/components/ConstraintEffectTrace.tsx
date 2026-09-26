/** Post-A′ #2: compact Requirement → recommend/DOE wiring chips. */
export default function ConstraintEffectTrace({
  items,
}: {
  items?: Array<{
    field: string;
    kind: string;
    label: string;
    status: string;
    consumers?: string[];
    detail?: string;
  }> | null;
}) {
  if (!items?.length) return null;
  return (
    <div
      className="mt-2 rounded border border-edge/50 bg-ink/30 px-2 py-1.5"
      data-testid="constraint-effect-trace"
    >
      <div className="text-[10px] text-slate-400 mb-1">约束追踪 · 哪些 brief 字段进了评分/DOE</div>
      <div className="flex flex-wrap gap-1">
        {items.slice(0, 16).map((t) => (
          <span
            key={t.field}
            title={[t.detail, (t.consumers || []).join(", ")].filter(Boolean).join(" · ") || undefined}
            className={`text-[9px] px-1.5 py-0.5 rounded border ${
              t.status === "wired"
                ? "border-emerald-500/30 text-emerald-300 bg-emerald-500/10"
                : t.status === "unwired"
                  ? "border-rose-500/30 text-rose-300 bg-rose-500/10"
                  : "border-slate-500/30 text-slate-400 bg-slate-500/10"
            }`}
            data-status={t.status}
          >
            {t.label}
            <span className="opacity-70">
              {t.status === "wired" ? " · 生效" : t.status === "unwired" ? " · 未接线" : " · 仅展示"}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
