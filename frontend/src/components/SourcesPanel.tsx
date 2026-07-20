import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../store";
import AddSourceModal from "./AddSourceModal";
import SourceTypePicker, { searchSourceTypes } from "./SourceTypePicker";

const ACCEPT = ".pdf,.docx,.doc,.xlsx,.pptx,.html,.htm,.txt,.md,.csv,.png,.jpg,.jpeg";

const FILTER_REASON_LABELS: Record<string, string> = {
  blocked_domain: "屏蔽域名",
  garbage_snippet: "无效摘要",
  near_duplicate: "近似重复",
  llm_judge: "LLM 质检",
};

function FilterReportBanner({
  report,
}: {
  report: {
    kept: number;
    dropped: number;
    dropped_by_reason: Record<string, number>;
    dropped_examples: string[];
  };
}) {
  const [open, setOpen] = useState(false);
  if (report.dropped <= 0) return null;
  return (
    <div className="shrink-0 text-[11px] text-slate-300 border border-slate-600/40 bg-slate-800/40 rounded px-2 py-1.5 leading-relaxed">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left flex items-center justify-between gap-2"
      >
        <span>
          质量过滤：保留 <span className="font-mono text-teal-300">{report.kept}</span> 条，剔除{" "}
          <span className="font-mono text-amber-300">{report.dropped}</span> 条低质量结果
        </span>
        <span className="text-slate-500 shrink-0">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="mt-1.5 space-y-1 text-[10px] text-slate-400 border-t border-edge/40 pt-1.5">
          <div className="flex flex-wrap gap-1">
            {Object.entries(report.dropped_by_reason).map(([reason, count]) => (
              <span
                key={reason}
                className="px-1 py-0.5 rounded border border-edge/60 bg-ink/50"
              >
                {FILTER_REASON_LABELS[reason] ?? reason}: {count}
              </span>
            ))}
          </div>
          {report.dropped_examples.length > 0 && (
            <ul className="list-disc list-inside text-slate-500 max-h-20 overflow-y-auto">
              {report.dropped_examples.map((ex, i) => (
                <li key={i} className="truncate" title={ex}>
                  {ex}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  patents: "专利",
  serpapi_lit: "Scholar",
  openalex: "OpenAlex",
  arxiv: "arXiv",
  s2: "Semantic Scholar",
  chemlit: "ChemCrow 文献",
  internet: "互联网",
  chemweb: "ChemCrow 网页",
  notebooklm: "NotebookLM",
};

function sourceLabel(name: string): string {
  return SOURCE_LABELS[name] || name;
}

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
    openSettings,
    uploadFiles,
    searchBusy,
    searchProgress,
    runDeepResearch,
    deepResearchBusy,
    deepResearchMessage,
    refreshKnowledgeBase,
    error,
    usedSeedFallback,
    filterReport,
    kbIngest,
    dismissKbIngest,
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
      openSettings: s.openSettings,
      uploadFiles: s.uploadFiles,
      searchBusy: s.searchBusy,
      searchProgress: s.searchProgress,
      runDeepResearch: s.runDeepResearch,
      deepResearchBusy: s.deepResearchBusy,
      deepResearchMessage: s.deepResearchMessage,
      refreshKnowledgeBase: s.refreshKnowledgeBase,
      error: s.error,
      usedSeedFallback: s.usedSeedFallback,
      filterReport: s.filterReport,
      kbIngest: s.kbIngest,
      dismissKbIngest: s.dismissKbIngest,
    }))
  );
  const fileInput = useRef<HTMLInputElement>(null);
  const [addSourceOpen, setAddSourceOpen] = useState(false);

  useEffect(() => {
    loadSourceStatus();
  }, [loadSourceStatus]);

  const searchableTypes = searchSourceTypes(sourceTypes);
  const canSearch =
    searchQuery.trim().length > 0 && searchableTypes.length > 0 && !searchBusy;

  const chemcrowStatus = sourceStatus["chemcrow"];
  const showChemcrowBadge =
    chemcrowStatus !== undefined &&
    (sourceTypes.includes("literature") || sourceTypes.includes("internet"));

  const kbDocByIdentifier: Record<string, { status: string; error?: string | null }> = {};
  if (kbIngest) {
    for (const d of kbIngest.docs) {
      kbDocByIdentifier[d.identifier] = { status: d.status, error: d.error };
    }
  }

  return (
    <aside className="glass rounded-xl p-4 flex flex-col gap-3 h-full overflow-hidden">
      <h2 className="text-sm uppercase tracking-widest text-accent2 shrink-0">
        资料来源 · Sources
      </h2>

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

      <div className="shrink-0">
        <span className="text-xs text-slate-400 block mb-1.5">信息类别 · Sources</span>
        <SourceTypePicker
          selected={sourceTypes}
          onChange={setSourceTypes}
          sourceStatus={sourceStatus}
        />
      </div>

      {showChemcrowBadge && chemcrowStatus && (
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
            <span className="text-slate-500 leading-relaxed">{chemcrowStatus.hint}</span>
          )}
        </div>
      )}

      {!searchBusy && error && (
        <div className="shrink-0 text-xs bg-red-500/10 border border-red-500/20 rounded p-2 text-red-300 leading-relaxed">
          ⚠ 检索失败：{error.replace(/^Error:\s*/, "")}
          {(error.includes("Failed to fetch") || error.includes("fetch")) && (
            <div className="mt-0.5 text-red-400/70">
              请确认后端已启动：
              <code className="text-red-300 bg-ink/60 rounded px-1">
                uvicorn app.main:app --port 8000
              </code>
            </div>
          )}
        </div>
      )}

      {usedSeedFallback && sources.length > 0 && !searchBusy && (
        <div className="shrink-0 text-[11px] text-amber-200/90 border border-amber-500/30 bg-amber-500/10 rounded px-2 py-1.5 leading-relaxed">
          当前结果含<strong className="font-semibold">离线示例摘要</strong>（非实时专利数据）。配置在线检索后可获取真实文献。
        </div>
      )}

      {filterReport && sources.length > 0 && !searchBusy && (
        <FilterReportBanner report={filterReport} />
      )}

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
        className="shrink-0 w-full bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-2 text-sm disabled:opacity-40"
      >
        {searchBusy
          ? searchProgress?.total
            ? `检索中（${searchProgress.total} 条）…`
            : "检索中…"
          : "开始检索"}
      </button>

      {searchBusy && searchProgress && (
        <div className="shrink-0 rounded-lg border border-accent/25 bg-accent/5 px-3 py-2.5 text-[11px]">
          <div className="flex items-center justify-between text-slate-400 mb-1.5">
            <span className="text-accent2 uppercase tracking-widest">实时检索</span>
            <span className="text-slate-300">{searchProgress.message}</span>
          </div>
          <div className="h-1.5 bg-edge rounded overflow-hidden mb-2">
            <div
              className="h-full bg-accent/80 transition-all duration-300 animate-pulse"
              style={{
                width: `${Math.min(100, Math.max(8, (searchProgress.total / 300) * 100))}%`,
              }}
            />
          </div>
          <div className="flex flex-wrap gap-1.5">
            {searchProgress.sourcesDone.map((s) => (
              <span
                key={`done-${s}`}
                className="px-1.5 py-0.5 rounded bg-teal-500/15 text-teal-300 border border-teal-500/20"
              >
                ✓ {sourceLabel(s)}
              </span>
            ))}
            {searchProgress.source &&
              !searchProgress.sourcesDone.includes(searchProgress.source) && (
                <span className="px-1.5 py-0.5 rounded bg-accent/15 text-accent border border-accent/30 animate-pulse">
                  … {sourceLabel(searchProgress.source)}
                </span>
              )}
            {searchProgress.sourcesPending
              .filter((s) => s !== searchProgress.source)
              .slice(0, 6)
              .map((s) => (
                <span
                  key={`pending-${s}`}
                  className="px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-500 border border-edge/60"
                >
                  {sourceLabel(s)}
                </span>
              ))}
          </div>
        </div>
      )}

      {kbIngest && (
        <div className="shrink-0 rounded-lg border border-teal-500/25 bg-teal-500/5 px-3 py-2 text-[11px]">
          <div className="flex items-center justify-between gap-2">
            <span className="text-teal-300 uppercase tracking-widest shrink-0">
              📚 知识库构建
            </span>
            <span
              className={`text-slate-400 truncate ${kbIngest.active ? "animate-pulse" : ""}`}
              title={kbIngest.message}
            >
              {kbIngest.message}
            </span>
            {!kbIngest.active && (
              <button
                onClick={dismissKbIngest}
                className="shrink-0 text-slate-600 hover:text-slate-300"
                title="关闭"
              >
                ×
              </button>
            )}
          </div>
          {kbIngest.total > 0 && (
            <div className="mt-1.5 h-1 bg-edge rounded overflow-hidden">
              <div
                className={`h-full transition-all duration-300 ${
                  kbIngest.active ? "bg-teal-400/80 animate-pulse" : "bg-teal-500/70"
                }`}
                style={{
                  width: `${Math.min(100, Math.max(6, (kbIngest.done / kbIngest.total) * 100))}%`,
                }}
              />
            </div>
          )}
        </div>
      )}

      <button
        type="button"
        onClick={() => void runDeepResearch()}
        disabled={deepResearchBusy || searchBusy || !searchQuery.trim()}
        className="shrink-0 w-full border border-accent2/40 bg-accent2/10 hover:bg-accent2/20 text-accent2 font-semibold rounded px-3 py-1.5 text-sm disabled:opacity-40 flex items-center justify-center gap-1.5"
      >
        {deepResearchBusy
          ? `🔬 ${deepResearchMessage || "深度研究中…"}`
          : "🔬 深度研究"}
      </button>

      <div className="shrink-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
        <button
          type="button"
          onClick={() => void refreshKnowledgeBase()}
          disabled={searchBusy || !searchQuery.trim()}
          className="hover:text-accent2 disabled:opacity-40"
        >
          📥 补充知识库
        </button>
        <span className="text-slate-700">·</span>
        <button
          type="button"
          onClick={() => setAddSourceOpen(true)}
          className="hover:text-accent"
        >
          + 添加数据源
        </button>
        <span className="text-slate-700">·</span>
        <button
          type="button"
          onClick={() => openSettings("api")}
          className="hover:text-accent"
        >
          API 配置 →
        </button>
        <span className="text-slate-700">·</span>
        <button
          type="button"
          onClick={() => openSettings("deps")}
          className="hover:text-accent"
        >
          NotebookLM / 依赖 →
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
              </div>
            );
          })
        )}
        {searchBusy && sources.length > 0 && (
          <p className="text-[10px] text-slate-500 text-center py-1 animate-pulse">
            继续加载更多结果…
          </p>
        )}
      </div>
    </aside>
  );
}
