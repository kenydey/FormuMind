import type { SearchSourceType, SourceStatus } from "../api";

export const SOURCE_TYPES: { id: SearchSourceType; label: string; icon: string }[] = [
  { id: "patents", label: "专利 Patents", icon: "📄" },
  { id: "literature", label: "文献 Literature", icon: "📚" },
  { id: "internet", label: "互联网 Internet", icon: "🌐" },
  { id: "local", label: "本地文件 Local", icon: "📎" },
  { id: "notebooklm", label: "NotebookLM", icon: "📓" },
];

export type SourceStatusColor = "green" | "yellow" | "red" | "gray";

/** Map backend availability to UI dot color (docs: green/yellow/red). */
export function sourceStatusColor(st?: SourceStatus): SourceStatusColor {
  if (!st) return "gray";
  if (!st.available) return "red";
  if (st.offline_fallback) return "yellow";
  return "green";
}

const DOT_CLASS: Record<SourceStatusColor, string> = {
  green: "bg-emerald-400",
  yellow: "bg-amber-400",
  red: "bg-rose-500",
  gray: "bg-slate-600",
};

function statusTitle(st: SourceStatus | undefined, fallback: string): string {
  if (!st) return fallback;
  if (st.hint) return st.hint;
  const color = sourceStatusColor(st);
  if (color === "green") return "在线可用";
  if (color === "yellow") return "离线种子回退";
  return "未安装或未配置";
}

export default function SourceTypePicker({
  selected,
  onChange,
  sourceStatus = {},
  showStatus = true,
  compact,
}: {
  selected: SearchSourceType[];
  onChange: (types: SearchSourceType[]) => void;
  sourceStatus?: Record<string, SourceStatus>;
  showStatus?: boolean;
  compact?: boolean;
}) {
  function toggle(id: SearchSourceType) {
    if (selected.includes(id)) {
      onChange(selected.filter((x) => x !== id));
    } else {
      onChange([...selected, id]);
    }
  }

  return (
    <div className={compact ? "grid grid-cols-2 sm:grid-cols-3 gap-1.5" : "grid grid-cols-2 gap-1.5"}>
      {SOURCE_TYPES.map((t) => {
        const on = selected.includes(t.id);
        const st = sourceStatus[t.id];
        const dot = sourceStatusColor(st);
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => toggle(t.id)}
            title={statusTitle(st, t.label)}
            className={`flex items-center gap-1.5 text-left text-[11px] border rounded px-2 py-1.5 transition-colors ${
              on
                ? "border-accent/50 bg-accent/10 text-accent"
                : "border-edge text-slate-400 hover:border-accent/30"
            }`}
          >
            {showStatus && (
              <span
                className={`w-2 h-2 rounded-full shrink-0 ${DOT_CLASS[dot]}`}
                aria-hidden
              />
            )}
            <span>{t.icon}</span>
            <span className="truncate">{t.label}</span>
            {on && <span className="ml-auto text-accent text-[10px]">✓</span>}
          </button>
        );
      })}
    </div>
  );
}

export function isLocalEvidence(source: string): boolean {
  const s = source.toLowerCase();
  return s === "local" || s.includes("upload") || s.includes("ingest");
}

/** Source types sent to /api/search/stream (local is ingest-only). */
export function searchSourceTypes(selected: SearchSourceType[]): SearchSourceType[] {
  return selected.filter((t) => t !== "local");
}
