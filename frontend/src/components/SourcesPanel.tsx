import { useRef } from "react";
import { useStore } from "../store";
import type { SearchSourceType } from "../api";

const SOURCE_TYPES: { id: SearchSourceType; label: string; icon: string }[] = [
  { id: "patents", label: "专利 Patents", icon: "📄" },
  { id: "literature", label: "文献 Literature", icon: "📚" },
  { id: "internet", label: "互联网 Internet", icon: "🌐" },
  { id: "local", label: "本地文件 Local", icon: "📎" },
];

const ACCEPT = ".pdf,.docx,.doc,.xlsx,.pptx,.html,.htm,.txt,.md,.csv,.png,.jpg,.jpeg";

function iconForSource(source: string): string {
  const s = source.toLowerCase();
  if (s.includes("patent")) return "📄";
  if (s.includes("arxiv") || s.includes("semantic") || s.includes("literature") || s.includes("paper"))
    return "📚";
  if (s.includes("web") || s.includes("duck") || s.includes("internet")) return "🌐";
  return "📎";
}

export default function SourcesPanel() {
  const {
    searchQuery,
    setSearchQuery,
    sourceTypes,
    setSourceTypes,
    sources,
    removeSource,
    clearSources,
    searchSources,
    uploadFile,
    searchBusy,
  } = useStore();
  const fileInput = useRef<HTMLInputElement>(null);

  function toggleType(t: SearchSourceType) {
    if (sourceTypes.includes(t)) {
      setSourceTypes(sourceTypes.filter((x) => x !== t));
    } else {
      setSourceTypes([...sourceTypes, t]);
    }
  }

  const localSelected = sourceTypes.includes("local");

  return (
    <aside className="glass rounded-xl p-4 flex flex-col gap-3 h-full overflow-hidden">
      <h2 className="text-sm uppercase tracking-widest text-accent2 shrink-0">
        资料来源 · Sources
      </h2>

      {/* Research topic / prompt */}
      <label className="block shrink-0">
        <span className="text-xs text-slate-400">研究主题 · Topic</span>
        <textarea
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          rows={3}
          placeholder="输入研究主题或提示词，例如：环保型水性防腐涂料配方研究…"
          className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm resize-none focus:border-accent/50 outline-none"
        />
      </label>

      {/* Source type checkboxes */}
      <div className="grid grid-cols-2 gap-1.5 shrink-0">
        {SOURCE_TYPES.map((t) => {
          const on = sourceTypes.includes(t.id);
          return (
            <button
              key={t.id}
              onClick={() => toggleType(t.id)}
              className={`flex items-center gap-1.5 text-xs rounded px-2 py-1.5 border transition-colors ${
                on
                  ? "border-accent/50 bg-accent/10 text-accent"
                  : "border-edge text-slate-400 hover:border-slate-600"
              }`}
            >
              <span
                className={`w-3.5 h-3.5 rounded-sm border flex items-center justify-center text-[9px] ${
                  on ? "bg-accent border-accent text-ink" : "border-slate-600"
                }`}
              >
                {on ? "✓" : ""}
              </span>
              <span>{t.icon}</span>
              <span className="truncate">{t.label}</span>
            </button>
          );
        })}
      </div>

      {/* Upload + search */}
      <div className="flex gap-2 shrink-0">
        {localSelected && (
          <>
            <input
              ref={fileInput}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) uploadFile(f);
                e.target.value = "";
              }}
            />
            <button
              onClick={() => fileInput.current?.click()}
              disabled={searchBusy}
              className="text-xs border border-edge text-slate-400 rounded px-2.5 py-1.5 hover:text-accent hover:border-accent/40 disabled:opacity-40"
              title="上传本地文件"
            >
              ⬆ 上传
            </button>
          </>
        )}
        <button
          onClick={searchSources}
          disabled={searchBusy || sourceTypes.length === 0}
          className="flex-1 bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-1.5 text-sm disabled:opacity-40"
        >
          {searchBusy ? "检索中…" : "开始检索"}
        </button>
      </div>

      <div className="border-t border-edge shrink-0" />

      {/* Loaded sources list */}
      <div className="flex items-center justify-between shrink-0">
        <span className="text-xs text-slate-400 uppercase tracking-wider">
          已加载资料 · {sources.length}
        </span>
        {sources.length > 0 && (
          <button
            onClick={clearSources}
            className="text-[10px] text-slate-500 hover:text-rose-400"
          >
            清空
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto min-h-0 flex flex-col gap-1.5">
        {sources.length === 0 ? (
          <p className="text-slate-600 text-xs">
            勾选信息类别并填写主题后点击「开始检索」，或上传本地文件。检索完成后即可在中栏向资料提问。
          </p>
        ) : (
          sources.map((e) => {
            const id = e.identifier || e.title;
            return (
              <div
                key={id}
                className="group flex items-start gap-2 bg-ink/50 border border-edge/60 rounded px-2 py-1.5 text-[11px]"
              >
                <span className="shrink-0">{iconForSource(e.source)}</span>
                <div className="min-w-0 flex-1">
                  <div className="text-slate-300 truncate" title={e.title}>
                    {e.title}
                  </div>
                  <div className="text-slate-600 truncate">{e.source}</div>
                </div>
                <button
                  onClick={() => removeSource(id)}
                  className="shrink-0 text-slate-600 hover:text-rose-400 opacity-0 group-hover:opacity-100 transition-opacity"
                  title="移除"
                >
                  ×
                </button>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
