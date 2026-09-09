import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, type Evidence, type EmbodimentDraft, type KBSourceItem } from "../api";
import { useStore } from "../store";
import { idsMatch, patentIdAliases } from "../utils/patentIds";
import AddSourceModal from "./AddSourceModal";
import SourceDetailModal from "./SourceDetailModal";
import EmbodimentDraftModal from "./EmbodimentDraftModal";
import KgRelationPanel from "./KgRelationPanel";
import RagPrewarmBar from "./RagPrewarmBar";
import WikiBrowserModal from "./WikiBrowserModal";
import SourceTypePicker, { searchSourceTypes } from "./SourceTypePicker";
import { CANCEL_BUTTON_CLASS, coldStartMessage } from "../hooks/useTaskCancel";

const ACCEPT = ".pdf,.docx,.doc,.xlsx,.pptx,.html,.htm,.txt,.md,.csv,.png,.jpg,.jpeg";

function iconForSource(source: string): string {
  const s = source.toLowerCase();
  if (s.includes("surechembl")) return "🧪";
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

function canIngestFulltext(e: Evidence): boolean {
  if (!e.identifier || e.is_seed_corpus) return false;
  const s = (e.source || "").toLowerCase();
  if (s.includes("surechembl") || s.includes("patent") || s === "uspto" || s === "epo") {
    return true;
  }
  if (
    s.includes("literature") ||
    s.includes("openalex") ||
    s.includes("arxiv") ||
    s.includes("semantic") ||
    s.includes("crossref")
  ) {
    return !!(
      e.is_oa ||
      e.oa_pdf_url ||
      /10\.\d{4,9}\//.test(e.identifier) ||
      /arxiv/i.test(e.identifier)
    );
  }
  return false;
}

type KbDocStatus = { status: string; error?: string | null; source_id?: string | null };

function resolveSourceId(
  e: Evidence,
  kbIngestDocs: { identifier: string; source_id?: string | null }[] | undefined,
  kbDocs: KBSourceItem[],
  local?: Record<string, KbDocStatus>
): string | undefined {
  const keys = [
    ...patentIdAliases(e.identifier),
    ...patentIdAliases(e.url),
    e.identifier,
  ].filter(Boolean) as string[];
  if (local) {
    for (const k of keys) {
      const hit = local[k];
      if (hit?.source_id) return hit.source_id;
    }
  }
  for (const d of kbIngestDocs || []) {
    if (!d.source_id) continue;
    if (idsMatch(d.identifier, e.identifier) || idsMatch(d.identifier, e.url)) {
      return d.source_id;
    }
  }
  for (const d of kbDocs) {
    if (!d.origin_url && !d.id) continue;
    if (idsMatch(d.origin_url, e.identifier) || idsMatch(d.origin_url, e.url)) {
      return d.id;
    }
  }
  return undefined;
}

function resolveKbRowStatus(
  e: Evidence,
  kbIngest: { docs: { identifier: string; status: string; error?: string | null; source_id?: string | null }[] } | null,
  kbDocs: KBSourceItem[],
  local: Record<string, KbDocStatus>
): KbDocStatus | undefined {
  const keys = patentIdAliases(e.identifier);
  for (const k of keys) {
    if (local[k]) return local[k];
  }
  if (local[e.identifier]) return local[e.identifier];
  if (kbIngest) {
    for (const d of kbIngest.docs) {
      if (idsMatch(d.identifier, e.identifier) || idsMatch(d.identifier, e.url)) {
        return {
          status: d.status,
          error: d.error,
          source_id: d.source_id ?? null,
        };
      }
    }
  }
  const sid = resolveSourceId(e, undefined, kbDocs, undefined);
  if (sid) {
    return { status: "skipped", source_id: sid };
  }
  return undefined;
}

export default function SourcesPanel() {
  const {
    searchQuery,
    setSearchQuery,
    sourceTypes,
    setSourceTypes,
    setOpenModal,
    notebooklmNotebookId,
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
      setOpenModal: s.setOpenModal,
      notebooklmNotebookId: s.notebooklmNotebookId,
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
  const [wikiOpen, setWikiOpen] = useState(false);
  const [detailDoc, setDetailDoc] = useState<{ title: string; sourceId: string } | null>(null);
  const [draftReview, setDraftReview] = useState<EmbodimentDraft | null>(null);
  const [schActionBusy, setSchActionBusy] = useState<string | null>(null);
  const [schActionMsg, setSchActionMsg] = useState<string | null>(null);
  const [localFtStatus, setLocalFtStatus] = useState<Record<string, KbDocStatus>>({});
  // 知识库文档(2026-09-05): 已导入语料列表 —— 项目视图含全局文档(project_id OR NULL),
  // 不依赖易被覆盖的 payload.sources —— 资料可见性的权威来源。
  const [kbDocs, setKbDocs] = useState<KBSourceItem[]>([]);
  const [eligibleIds, setEligibleIds] = useState<Record<string, boolean>>({});
  const activeProjectId = useStore((s) => s.activeProjectId);

  async function ingestSurechemblKg(e: Evidence) {
    const docId = e.identifier;
    if (!docId) return;
    setSchActionBusy(`kg:${docId}`);
    setSchActionMsg(null);
    try {
      const res = await api.surechemblIngestDocument({
        doc_id: docId,
        title: e.title,
        assignee: e.assignee,
        pub_date: e.pub_date,
        url: e.url,
        fetch_chemistry: true,
      });
      setSchActionMsg(
        `图谱已更新 · ${docId}：${res.entities} 实体 / ${res.links} 边（${res.link_type}）`
      );
    } catch (err) {
      setSchActionMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setSchActionBusy(null);
    }
  }

  async function ingestEvidenceFulltext(e: Evidence) {
    const docId = e.identifier;
    if (!docId) return;
    setSchActionBusy(`ft:${docId}`);
    setSchActionMsg(null);
    setLocalFtStatus((prev) => ({
      ...prev,
      [docId]: { status: "fetching", source_id: prev[docId]?.source_id ?? null },
    }));
    try {
      const res = await api.ingestEvidence({
        identifier: e.identifier,
        title: e.title,
        url: e.url,
        url_alt: e.url_alt,
        source: e.source,
        project_id: activeProjectId,
        assignee: e.assignee,
        pub_date: e.pub_date,
        snippet: e.snippet,
        oa_pdf_url: e.oa_pdf_url,
        is_oa: e.is_oa,
        relevance: e.relevance,
      });
      const status = res.status || (res.ok ? "indexed" : "failed");
      setLocalFtStatus((prev) => ({
        ...prev,
        [docId]: {
          status,
          source_id: res.source_id ?? null,
          error: res.reason ?? null,
        },
      }));
      if (res.ok) {
        setSchActionMsg(
          status === "skipped"
            ? `已在库 · ${res.canonical_id || docId}`
            : `全文已入库 · ${res.canonical_id || docId}`
        );
        // Refresh KB list so P3.1 eligibility + extract light up.
        try {
          const list = await api.kbSources(activeProjectId, 200);
          setKbDocs(list.sources ?? []);
        } catch {
          /* ignore refresh errors */
        }
      } else {
        setSchActionMsg(res.reason || "入库全文失败");
      }
    } catch (err) {
      setLocalFtStatus((prev) => ({
        ...prev,
        [docId]: {
          status: "failed",
          source_id: prev[docId]?.source_id ?? null,
          error: err instanceof Error ? err.message : String(err),
        },
      }));
      setSchActionMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setSchActionBusy(null);
    }
  }

  async function extractFulltextDraft(sourceId: string, opts?: { surechemblHint?: boolean }) {
    setSchActionBusy(`emb:${sourceId}`);
    setSchActionMsg(null);
    try {
      const res = await api.extractEmbodimentDraft({
        source_id: sourceId,
        surechembl_hint: opts?.surechemblHint,
      });
      if (!res.ok || !res.draft) {
        setSchActionMsg(res.reason || "无法提取实施例草稿");
        return;
      }
      setDraftReview(res.draft);
    } catch (err) {
      setSchActionMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setSchActionBusy(null);
    }
  }

  async function extractSurechemblDraft(e: Evidence) {
    const docId = e.identifier;
    if (!docId) return;
    const mappedId = resolveSourceId(e, kbIngest?.docs, kbDocs, localFtStatus);
    if (mappedId && eligibleIds[mappedId]) {
      await extractFulltextDraft(mappedId, { surechemblHint: true });
      return;
    }
    setSchActionBusy(`draft:${docId}`);
    setSchActionMsg(null);
    try {
      const res = await api.surechemblExtractExampleDraft({
        doc_id: docId,
        title: e.title,
        assignee: e.assignee,
        pub_date: e.pub_date,
        url: e.url,
      });
      if (!res.ok || !res.draft) {
        setSchActionMsg(
          (res.reason || "无法提取实施例草稿") +
            " · 提示：入库全文后可提取真实比重"
        );
        return;
      }
      setDraftReview(res.draft);
    } catch (err) {
      setSchActionMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setSchActionBusy(null);
    }
  }

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

  useEffect(() => {
    const ids = kbDocs.map((d) => d.id).filter(Boolean);
    if (!ids.length) {
      setEligibleIds({});
      return;
    }
    let cancelled = false;
    api
      .embodimentEligibility(ids.slice(0, 100))
      .then((res) => {
        if (cancelled) return;
        const map: Record<string, boolean> = {};
        for (const it of res.items || []) {
          map[it.source_id] = !!it.eligible;
        }
        setEligibleIds(map);
      })
      .catch(() => {
        if (!cancelled) setEligibleIds({});
      });
    return () => {
      cancelled = true;
    };
  }, [kbDocs]);

  const searchableTypes = searchSourceTypes(sourceTypes);
  const canSearch =
    searchQuery.trim().length > 0 &&
    searchableTypes.length > 0 &&
    !searchBusy &&
    !deepResearchBusy;

  const kbDocByIdentifier: Record<string, KbDocStatus> = {};
  for (const e of sources) {
    const st = resolveKbRowStatus(e, kbIngest, kbDocs, localFtStatus);
    if (st && e.identifier) {
      kbDocByIdentifier[e.identifier] = st;
    }
  }

  return (
    <aside className="glass rounded-xl p-4 flex flex-col gap-3 h-full overflow-hidden">
      <h2 className="text-sm uppercase tracking-widest text-accent2 shrink-0 flex items-center justify-between gap-2">
        <span>资料来源 · Sources</span>
        <button
          type="button"
          onClick={() => setWikiOpen(true)}
          className="text-[10px] normal-case tracking-normal px-2 py-0.5 rounded border border-accent/40 text-accent hover:bg-accent/10"
          title="浏览 LLM Wiki 凝练页"
        >
          Wiki
        </button>
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
          onChange={(types) => {
            const enabling =
              types.includes("notebooklm") && !sourceTypes.includes("notebooklm");
            setSourceTypes(types);
            if (enabling && !notebooklmNotebookId.trim()) {
              setOpenModal("notebooklm-setup");
            }
          }}
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
      <WikiBrowserModal open={wikiOpen} onClose={() => setWikiOpen(false)} />

      <div className="border-t border-edge shrink-0" />

      <div className="flex items-center justify-between shrink-0 gap-2">
        <span className="text-xs text-slate-400 uppercase tracking-wider">
          知识库资料 · {kbDocs.length || sources.length}
          {sources.length > 0 && (
            <span className="text-slate-600 normal-case">
              {" "}
              （已选 {selectedSources.length} 检索证据）
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
            const rowKb = kbDocByIdentifier[e.identifier];
            const mappedSourceId = resolveSourceId(
              e,
              kbIngest?.docs,
              kbDocs,
              localFtStatus
            );
            const fulltextEligible = !!(mappedSourceId && eligibleIds[mappedSourceId]);
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
                    {rowKb && (
                      <KbDocBadge status={rowKb.status} error={rowKb.error} />
                    )}
                  </div>
                  <div className="text-slate-600 truncate flex items-center gap-1.5">
                    <span className="truncate">{e.source}</span>
                    {e.identifier && (
                      <span className="font-mono text-[10px] text-slate-500 shrink-0">
                        {e.identifier}
                      </span>
                    )}
                    {(e.url || e.url_alt) && (
                      <span className="shrink-0 flex items-center gap-1">
                        {e.url && (
                          <a
                            href={e.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-accent/90 hover:underline"
                            title="打开 Google Patents"
                            onClick={(ev) => ev.stopPropagation()}
                          >
                            Patents
                          </a>
                        )}
                        {e.url_alt && (
                          <a
                            href={e.url_alt}
                            target="_blank"
                            rel="noreferrer"
                            className="text-accent/70 hover:underline"
                            title="SureChEMBL 文档页"
                            onClick={(ev) => ev.stopPropagation()}
                          >
                            SureChEMBL
                          </a>
                        )}
                      </span>
                    )}
                    {canIngestFulltext(e) && (
                      <button
                        type="button"
                        data-testid={`ingest-fulltext-${e.identifier}`}
                        disabled={
                          schActionBusy === `ft:${e.identifier}` ||
                          rowKb?.status === "fetching" ||
                          rowKb?.status === "indexing"
                        }
                        onClick={(ev) => {
                          ev.stopPropagation();
                          void ingestEvidenceFulltext(e);
                        }}
                        className="text-teal-300/90 hover:underline disabled:opacity-40"
                        title="下载并入库全文到知识库（与入库图谱独立；成功后可提取真实比重）"
                      >
                        {schActionBusy === `ft:${e.identifier}` ||
                        rowKb?.status === "fetching" ||
                        rowKb?.status === "indexing"
                          ? "入库中…"
                          : rowKb?.source_id
                            ? "再入库全文"
                            : "入库全文"}
                      </button>
                    )}
                    {e.source === "surechembl" && e.identifier && (
                      <span className="shrink-0 flex items-center gap-1">
                        <button
                          type="button"
                          data-testid={`surechembl-ingest-kg-${e.identifier}`}
                          disabled={schActionBusy === `kg:${e.identifier}`}
                          onClick={(ev) => {
                            ev.stopPropagation();
                            void ingestSurechemblKg(e);
                          }}
                          className="text-accent2/90 hover:underline disabled:opacity-40"
                          title="入库知识图谱（patent:scpn / chem:surechembl）"
                        >
                          {schActionBusy === `kg:${e.identifier}` ? "入库中…" : "入库图谱"}
                        </button>
                        <button
                          type="button"
                          data-testid={`surechembl-extract-draft-${e.identifier}`}
                          disabled={
                            schActionBusy === `draft:${e.identifier}` ||
                            (!!mappedSourceId && schActionBusy === `emb:${mappedSourceId}`)
                          }
                          onClick={(ev) => {
                            ev.stopPropagation();
                            void extractSurechemblDraft(e);
                          }}
                          className="text-amber-300/90 hover:underline disabled:opacity-40"
                          title={
                            fulltextEligible
                              ? "已有入库全文：提取真实比重实施例草稿"
                              : "提取实施例草稿（无全文时为占位均分；入库全文后可提取真实比重）"
                          }
                        >
                          {schActionBusy === `draft:${e.identifier}` ||
                          (!!mappedSourceId && schActionBusy === `emb:${mappedSourceId}`)
                            ? "提取中…"
                            : "提取实施例草稿"}
                        </button>
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => removeSource(id)}
                  className="shrink-0 text-slate-600 hover:text-rose-400 opacity-0 group-hover:opacity-100 transition-opacity"
                  title="移除"
                >
                  ×
                </button>
                {(rowKb?.source_id || mappedSourceId) && (
                  <button
                    onClick={() =>
                      setDetailDoc({
                        title: e.title || e.identifier || "资料",
                        sourceId: (rowKb?.source_id || mappedSourceId)!,
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
                  {eligibleIds[d.id] && (
                    <button
                      type="button"
                      data-testid={`embodiment-extract-${d.id}`}
                      disabled={schActionBusy === `emb:${d.id}`}
                      onClick={() => void extractFulltextDraft(d.id)}
                      className="shrink-0 text-[10px] text-amber-300/90 hover:underline disabled:opacity-40"
                      title="从已入库全文提取实施例草稿（需人工确认）"
                    >
                      {schActionBusy === `emb:${d.id}` ? "提取中…" : "提取实施例草稿"}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      {schActionMsg && (
        <div
          className="shrink-0 text-[10px] text-slate-400 border border-edge/50 rounded px-2 py-1"
          data-testid="surechembl-action-msg"
        >
          {schActionMsg}
          <button
            type="button"
            className="ml-2 text-slate-600 hover:text-slate-400"
            onClick={() => setSchActionMsg(null)}
          >
            ×
          </button>
        </div>
      )}
      {detailDoc && (
        <SourceDetailModal
          title={detailDoc.title}
          sourceId={detailDoc.sourceId}
          onClose={() => setDetailDoc(null)}
        />
      )}
      {draftReview && (
        <EmbodimentDraftModal
          draft={draftReview}
          onClose={() => setDraftReview(null)}
          onConfirmed={(note) => setSchActionMsg(note)}
        />
      )}
    </aside>
  );
}