import type { SearchSourceType } from "../api";

export const SOURCE_TYPES: { id: SearchSourceType; label: string; icon: string }[] = [
  { id: "patents", label: "专利 Patents", icon: "📄" },
  { id: "literature", label: "文献 Literature", icon: "📚" },
  { id: "internet", label: "互联网 Internet", icon: "🌐" },
  { id: "local", label: "本地文件 Local", icon: "📎" },
  { id: "notebooklm", label: "NotebookLM", icon: "📓" },
];

export default function SourceTypePicker({
  selected,
  onChange,
  compact,
}: {
  selected: SearchSourceType[];
  onChange: (types: SearchSourceType[]) => void;
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
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => toggle(t.id)}
            className={`flex items-center gap-1.5 text-left text-[11px] border rounded px-2 py-1.5 transition-colors ${
              on
                ? "border-accent/50 bg-accent/10 text-accent"
                : "border-edge text-slate-400 hover:border-accent/30"
            }`}
          >
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
