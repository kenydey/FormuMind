import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, type KBSourceItem } from "../api";
import { useStore } from "../store";
import AddSourceModal from "./AddSourceModal";
import SourceDetailModal from "./SourceDetailModal";
import KgRelationPanel from "./KgRelationPanel";
import RagPrewarmBar from "./RagPrewarmBar";
import SourceTypePicker, { searchSourceTypes } from "./SourceTypePicker";
import { CANCEL_BUTTON_CLASS, coldStartMessage } from "../hooks/useTaskCancel";

const ACCEPT = ".pdf,.docx,.doc,.xlsx,.pptx,.html,.htm,.txt,.md,.csv,.png,.jpg,.jpeg";

function iconForSource(source: string): string {
  const s = source.toLowerCase();
  if (s.includes("patent")) return "📄";
  if (s.includes("chemcrow")) return "🧪";
  if (s.includes("arxiv") || s.includes("semantic") || s.includes("literature") || s.includes("paper"))
    return "📚";
  if (s.includes("web") || s.includes("duck") || s.includes("internet")) return "🌐";
  if (s.includes("notebooklm")) return "📓";
  return "📎";
}

/** Per-document badge for the background KB build (async ingest). */
const KB_STATUS_BADGES: Record<string, { label: string; cls: string; pulse?: boolean }> = {
  queued: { label: "待入库", cls: "text-slate-500 border-edge/60" },
  fetching: { label: "获取全文", cls: "text-amber-300 border-amber-500/40", pulse: true },
  indexing: { label: "入库中", cls: "text-amber-300 border-amber-500/40", pulse: true },
  indexed: { label: "已入库", cls: "text-teal-300 border-teal-500/40" },
  skipped: { label: "已在库", cls: "text-teal-500/80 border-teal-500/25" },
  failed: { label: "入库失败", cls: "text-rose-400 border-rose-500/40" },
};

function KbDocBadge({ status, error }: { status: string; error?: string | null }) {
  const badge = KB_STATUS_BADGES[status];
  if (!badge) return null;
  return (
    <span
      title={error || undefined}
      className={`shrink-0 text-[9px] border rounded px-1 ${badge.cls} ${
        badge.pulse ? "animate-pulse" : ""
      }`}
    >
      {badge.label}
    </span>
  );
}

export default function SourcesPanel() {
  const {
    searchQuery,
    setSearchQuery,
    sourceTypes,
    setSourceTypes,
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
    uploadFiles,
    searchBusy,
    searchProgress,
    runDeepResearch,
    cancelDeepResearch,
    deepResearchBusy,
    deepResearchStage,
    deepResearchMessage,
    kbIngest,
  } = useStore(
    useShallow((s) => ({
      searchQuery: s.searchQuery,
      setSearchQuery: s.setSearchQuery,
      sourceTypes: s.sourceTypes,
      setSourceTypes: s.setSourceTypes,
      sources: s.sources,
      selectedSources: s.selectedSources,
      sourceStatus: s.sourceStatus,
      removeSource: s.removeSource,
      clearSources: s.clearSources,
      toggleSourceSelected: s.toggleSourceSelected,
      selectAllSources: s.selectAllSources,
      deselectAllSources: s.deselectAllSources,
      searchSources: s.searchSources,
      loadSourceStatus: s.loadSourceStatus,
      uploadFiles: s.uploadFiles,
      searchBusy: s.searchBusy,
      searchProgress: s.searchProgress,
      runDeepResearch: s.runDeepResearch,
      cancelDeepResearch: s.cancelDeepResearch,
      deepResearchBusy: s.deepResearchBusy,
      deepResearchStage: s.deepResearchStage,
      deepResearchMessage: s.deepResearchMessage,
      kbIngest: s.kbIngest,
    }))
  );
  const fileInput = useRef<HTMLInputElement>(null);
  const [addSourceOpen, setAddSourceOpen] = useState(false);
  const [detailDoc, setDetailDoc] = useState<{ title: string; sourceId: string } | null>(null);
  // 知识库文档(2026-09-05): 已导入语料列表 —— 项目视图含全局文档(project_id OR NULL),
  // 不依赖易被覆盖的 payload.sources —— 资料可见性的权威来源。
  const [kbDocs, setKbDocs] = useState<KBSourceItem[]>([]);
  const activeProjectId = useStore((s) => s.activeProjectId);

  useEffect(() => {
    loadSourceStatus();
  }, [loadSourceStatus]);

  useEffect(() => {
    let cancelled = false;
    api.kbSources(activeProjectId, 200)
      .then((res) => {
        if (!cancelled) setKbDocs(res.sources ?? []);
      })
      .catch(() => {
        if (!cancelled) setKbDocs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [activeProjectId, kbIngest]);

  const searchableTypes = searchSourceTypes(sourceTypes);
  const canSearch =
    searchQuery.trim().length > 0 &&
    searchableTypes.length > 0 &&
    !searchBusy &&
    !deepResearchBusy;

  const kbDocByIdentifier: Record<
    string,
    { status: string; error?: string | null; source_id?: string | null }
  > = {};
  if (kbIngest) {
    for (const d of kbIngest.docs) {
      kbDocByIdentifier[d.identifier] = {
        status: d.status,
        error: d.error,
        source_id: d.source_id ?? null,
      };
    }
  }

  return (
    <aside className="glass rounded-xl p-4 flex flex-col gap-3 h-full overflow-hidden">
      <h2 className="text-sm uppercase tracking-widest text-accent2 shrink-0">
        资料来源 · Sources
      </h2>

      <RagPrewarmBar />

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

      <KgRelationPanel query={searchQuery} />

      <div className="shrink-0">
        <span className="text-xs text-slate-400 block mb-1.5">信息类别 · Sources</span>
        <SourceTypePicker
          selected={sourceTypes}
          onChange={setSourceTypes}
          sourceStatus={sourceStatus}
        />
      </div>

      <input
        ref={fileInput}
        type="file"
        accept={ACCEPT}
        multiple
        className="hidden"
        aria-label="上传本地文件"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) void uploadFiles(files);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        onClick={() => fileInput.current?.click()}
        disabled={searchBusy}
        className="shrink-0 w-full text-xs border border-edge text-slate-400 rounded px-2.5 py-1.5 hover:text-accent hover:border-accent/40 disabled:opacity-40"
      >
        ⬆ 上传本地文件
      </button>

      <button
        type="button"
        onClick={() => void searchSources()}
        disabled={!canSearch}
        data-testid="btn-search"
        className="shrink-0 w-full bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-2 text-sm disabled:opacity-40"
      >
        {searchBusy
          ? searchProgress?.total
            ? `检索中（${searchProgress.total} 条）…`
            : "检索中…"
          : "开始检索"}
      </button>

      <button
        type="button"
        onClick={() => void runDeepResearch()}
        disabled={deepResearchBusy || searchBusy || !searchQuery.trim()}
        className="shrink-0 w-full border border-accent2/40 bg-accent2/10 hover:bg-accent2/20 text-accent2 font-semibold rounded px-3 py-1.5 text-sm disabled:opacity-40 flex items-center justify-center gap-1.5"
      >
        {deepResearchBusy
          ? `🔬 ${coldStartMessage(deepResearchStage, deepResearchMessage, "深度研究中…")}`
          : "🔬 深度研究"}
      </button>
      {deepResearchBusy && (
        <button type="button" onClick={() => void cancelDeepResearch()} className={"w-full " + CANCEL_BUTTON_CLASS}>✕ 取消深度研究</button>
      )}

      <div className="shrink-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
        <button
          type="button"
          onClick={() => setAddSourceOpen(true)}
          className="hover:text-accent"
        >
          + 添加数据源
        </button>
      </div>

      <AddSourceModal open={addSourceOpen} onClose={() => setAddSourceOpen(false)} />

      <div className="border-t border-edge shrink-0" />

      <div className="flex items-center justify-between shrink-0 gap-2">
        <span className="text-xs text-slate-400 uppercase tracking-wider">
          已加载资料 · {sources.length}
          {sources.length > 0 && (
            <span className="text-slate-600 normal-case">
              {" "}
              （已选 {selectedSources.length}）
            </span>
          )}
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
          <p className="text-slate-600 text-xs leading-relaxed">
            {searchBusy
              ? "正在检索，匹配结果将实时出现在下方列表…"
              : "勾选信息类别并填写主题后点击「开始检索」，或上传本地文件。结果会逐条加载，无需等待全部完成。"}
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
                  <div className="text-slate-300 truncate flex items-center gap-1" title={e.title}>
                    {e.is_seed_corpus && (
                      <span className="shrink-0 text-[9px] text-amber-400 border border-amber-500/40 rounded px-1">
                        示例
                      </span>
                    )}
                    <span className="truncate">{e.title}</span>
                    {kbDocByIdentifier[e.identifier] && (
                      <KbDocBadge
                        status={kbDocByIdentifier[e.identifier].status}
                        error={kbDocByIdentifier[e.identifier].error}
                      />
                    )}
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
                {kbDocByIdentifier[e.identifier]?.source_id && (
                  <button
                    onClick={() =>
                      setDetailDoc({
                        title: e.title || e.identifier || "资料",
                        sourceId: kbDocByIdentifier[e.identifier].source_id!,
                      })
                    }
                    className="shrink-0 text-slate-600 hover:text-accent opacity-0 group-hover:opacity-100 transition-opacity"
                    title="查看切块 / 链入知识图谱"
                  >
                    🔎
                  </button>
                )}
              </div>
            );
          })
        )}
        {searchBusy && sources.length > 0 && (
          <p className="text-[10px] text-slate-500 text-center py-1 animate-pulse">
            继续加载更多结果…
          </p>
        )}
        {!searchBusy && kbDocs.length > 0 && (
          <div className="border-t border-edge/60 pt-2 mt-1">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1 flex items-center justify-between">
              <span>📚 知识库文档 · {kbDocs.length}</span>
              <span className="text-slate-600 normal-case">
                （已导入语料，可供检索）
              </span>
            </div>
            <div className="flex flex-col gap-1">
              {kbDocs.map((d) => (
                <div
                  key={d.id}
                  className="group flex items-center gap-2 rounded px-2 py-1 text-[11px] bg-ink/30 border border-edge/40"
                >
                  <span className="shrink-0">{iconForSource(d.source_kind)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="text-slate-300 truncate" title={d.title ?? ""}>
                      {d.title ?? d.filename}
                    </div>
                    <div className="text-slate-600 truncate text-[10px]">
                      {d.source_kind ?? "doc"}
                      {d.raw_text_chars ? ` · ${(d.raw_text_chars / 1000).toFixed(0)}k 字` : ""}
                      {d.extraction_status ? ` · ${d.extraction_status}` : ""}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      setDetailDoc({ title: d.title ?? d.filename ?? "资料", sourceId: d.id })
                    }
                    className="shrink-0 text-slate-600 hover:text-accent opacity-0 group-hover:opacity-100 transition-opacity"
                    title="查看切块 / 链入知识图谱"
                  >
                    🔎
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      {detailDoc && (
        <SourceDetailModal
          title={detailDoc.title}
          sourceId={detailDoc.sourceId}
          onClose={() => setDetailDoc(null)}
        />
      )}
    </aside>
  );
}