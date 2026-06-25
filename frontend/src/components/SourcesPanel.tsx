import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import AddSourceModal from "./AddSourceModal";

const ACCEPT = ".pdf,.docx,.doc,.xlsx,.pptx,.html,.htm,.txt,.md,.csv,.png,.jpg,.jpeg";

function iconForSource(source: string): string {
  const s = source.toLowerCase();
  if (s.includes("patent")) return "📄";
  // ChemCrow must be checked before "web" / "literature" since "ChemCrow-Web" contains "web"
  if (s.includes("chemcrow")) return "🧪";
  if (s.includes("arxiv") || s.includes("semantic") || s.includes("literature") || s.includes("paper"))
    return "📚";
  if (s.includes("web") || s.includes("duck") || s.includes("internet")) return "🌐";
  if (s.includes("notebooklm")) return "📓";
  return "📎";
}

export default function SourcesPanel() {
  const {
    searchQuery,
    setSearchQuery,
    sources,
    selectedSources,
    sourceStatus,
    removeSource,
    clearSources,
    toggleSourceSelected,
    selectAllSources,
    deselectAllSources,
    searchSources,
    loadSourceStatus,
    openSettings,
    uploadFiles,
    searchBusy,
    runDeepResearch,
    deepResearchBusy,
    deepResearchMessage,
    refreshKnowledgeBase,
    error,
  } = useStore();
  const fileInput = useRef<HTMLInputElement>(null);
  const [addSourceOpen, setAddSourceOpen] = useState(false);

  // Load source availability on mount so badges appear before first search
  useEffect(() => {
    loadSourceStatus();
  }, [loadSourceStatus]);

  const chemcrowStatus = sourceStatus["chemcrow"];
  const showChemcrowBadge = chemcrowStatus !== undefined;

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

      <p className="text-[10px] text-slate-500 shrink-0">
        专利/文献/互联网源由后端 <code className="text-accent2">FORMUMIND_FEDERATED_SOURCES</code> 配置；上传文件自动写入 ColBERT 索引。
      </p>

      {/* ChemCrow chemistry-enhanced retrieval badge */}
      {showChemcrowBadge && (
        <div
          className={`shrink-0 text-xs rounded p-2 border flex flex-col gap-0.5 ${
            chemcrowStatus.available
              ? "bg-teal-500/10 border-teal-500/20"
              : "bg-slate-800/60 border-edge"
          }`}
        >
          <span
            className={`font-medium flex items-center gap-1 ${
              chemcrowStatus.available ? "text-teal-300" : "text-slate-400"
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                chemcrowStatus.available ? "bg-teal-400" : "bg-slate-600"
              }`}
            />
            🧪 ChemCrow 化学增强检索
            {chemcrowStatus.available ? " · 已启用" : " · 未安装"}
          </span>
          {!chemcrowStatus.available && chemcrowStatus.hint && (
            <span className="text-slate-500 leading-relaxed">
              {chemcrowStatus.hint}
            </span>
          )}
          {!chemcrowStatus.available && (
            <>
              <code className="mt-0.5 text-[10px] text-slate-300 bg-ink/60 rounded px-1 py-0.5 block break-all">
                source .venv/bin/activate
              </code>
              <code className="mt-0.5 text-[10px] text-slate-300 bg-ink/60 rounded px-1 py-0.5 block break-all">
                pip install -e &apos;.[intel]&apos;
              </code>
            </>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2 shrink-0">
        <input
          ref={fileInput}
          type="file"
          accept={ACCEPT}
          multiple
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) void uploadFiles(files);
            e.target.value = "";
          }}
        />
        <button
          onClick={() => fileInput.current?.click()}
          disabled={searchBusy}
          className="text-xs border border-edge text-slate-400 rounded px-2.5 py-1.5 hover:text-accent hover:border-accent/40 disabled:opacity-40"
        >
          ⬆ 上传本地
        </button>
        <button
          onClick={() => void refreshKnowledgeBase()}
          disabled={searchBusy || !searchQuery.trim()}
          className="text-xs border border-accent2/40 text-accent2 rounded px-2.5 py-1.5 hover:bg-accent2/10 disabled:opacity-40"
        >
          📥 补充知识库
        </button>
        <button
          onClick={() => void searchSources()}
          disabled={searchBusy || !searchQuery.trim()}
          className="flex-1 min-w-[6rem] bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-1.5 text-sm disabled:opacity-40"
        >
          {searchBusy ? "检索中…" : "增量检索"}
        </button>
      </div>

      <button
        onClick={runDeepResearch}
        disabled={deepResearchBusy || searchBusy || !searchQuery.trim()}
        className="shrink-0 w-full border border-accent2/40 bg-accent2/10 hover:bg-accent2/20 text-accent2 font-semibold rounded px-3 py-1.5 text-sm disabled:opacity-40 flex items-center justify-center gap-1.5"
      >
        {deepResearchBusy
          ? `🔬 ${deepResearchMessage || "深度研究中…"}`
          : "🔬 深度研究 (CRAG SSE)"}
      </button>

      <button
        type="button"
        onClick={() => openSettings("deps")}
        className="shrink-0 w-full text-[11px] text-slate-500 hover:text-accent"
      >
        NotebookLM / 依赖配置 →
      </button>

      <button
        type="button"
        onClick={() => setAddSourceOpen(true)}
        className="shrink-0 w-full border border-edge hover:border-accent/40 text-slate-300 hover:text-accent rounded px-3 py-1.5 text-sm flex items-center justify-center gap-1"
      >
        + 添加数据源
      </button>

      <AddSourceModal open={addSourceOpen} onClose={() => setAddSourceOpen(false)} />

      {/* Search error — shown when backend is unreachable or the API returns an error */}
      {!searchBusy && error && (
        <div className="shrink-0 text-xs bg-red-500/10 border border-red-500/20 rounded p-2 text-red-300 leading-relaxed">
          ⚠ 检索失败：{error.replace(/^Error:\s*/, "")}
          {(error.includes("Failed to fetch") || error.includes("fetch")) && (
            <div className="mt-0.5 text-red-400/70">
              请确认后端已启动：<code className="text-red-300 bg-ink/60 rounded px-1">uvicorn app.main:app --port 8000</code>
            </div>
          )}
        </div>
      )}

      <div className="border-t border-edge shrink-0" />

      {/* Loaded sources list */}
      <div className="flex items-center justify-between shrink-0 gap-2">
        <span className="text-xs text-slate-400 uppercase tracking-wider">
          已加载资料 · 已选 {selectedSources.length}/{sources.length}
        </span>
        {sources.length > 0 && (
          <div className="flex items-center gap-2">
            <button
              onClick={
                selectedSources.length === sources.length
                  ? deselectAllSources
                  : selectAllSources
              }
              className="text-[10px] text-slate-500 hover:text-accent"
            >
              {selectedSources.length === sources.length ? "取消全选" : "全选"}
            </button>
            <button
              onClick={clearSources}
              className="text-[10px] text-slate-500 hover:text-rose-400"
            >
              清空
            </button>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto min-h-0 flex flex-col gap-1.5">
        {sources.length === 0 ? (
          <p className="text-slate-600 text-xs">
            填写主题后点击「补充知识库」或「深度研究」；上传文件会自动索引。检索结果将显示在下方列表。
          </p>
        ) : (
          sources.map((e) => {
            const id = e.identifier || e.title;
            const selected = selectedSources.includes(id);
            return (
              <div
                key={id}
                className={`group flex items-start gap-2 border rounded px-2 py-1.5 text-[11px] transition-colors ${
                  selected ? "bg-ink/50 border-edge/60" : "bg-ink/30 border-edge/40 opacity-60"
                }`}
              >
                <button
                  onClick={() => toggleSourceSelected(id)}
                  className={`mt-0.5 w-3.5 h-3.5 rounded-sm border flex items-center justify-center text-[9px] shrink-0 ${
                    selected ? "bg-accent border-accent text-ink" : "border-slate-600"
                  }`}
                  title={selected ? "已选用于问答" : "未选用"}
                >
                  {selected ? "✓" : ""}
                </button>
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
