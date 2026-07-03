import { useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../store";
import { api, type SearchSourceType } from "../api";
import { SOURCE_LIMIT } from "../projectWorkspace";
import Modal from "./Modal";

const MODAL_SOURCE_TYPES: { id: SearchSourceType; label: string; icon: string }[] = [
  { id: "patents", label: "专利", icon: "📄" },
  { id: "literature", label: "文献", icon: "📚" },
  { id: "internet", label: "互联网", icon: "🌐" },
];

const ACCEPT = ".pdf,.docx,.doc,.xlsx,.pptx,.html,.htm,.txt,.md,.csv,.png,.jpg,.jpeg";

export default function AddSourceModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const {
    searchQuery,
    sourceTypes,
    sources,
    selectedSources,
    searchBusy,
    setSourceTypes,
    searchSources,
    uploadFiles,
    addSources,
  } = useStore(
    useShallow((s) => ({
      searchQuery: s.searchQuery,
      sourceTypes: s.sourceTypes,
      sources: s.sources,
      selectedSources: s.selectedSources,
      searchBusy: s.searchBusy,
      setSourceTypes: s.setSourceTypes,
      searchSources: s.searchSources,
      uploadFiles: s.uploadFiles,
      addSources: s.addSources,
    }))
  );

  const [keyword, setKeyword] = useState(searchQuery);
  const [modalTypes, setModalTypes] = useState<SearchSourceType[]>(
    sourceTypes.filter((t) => t !== "local" && t !== "notebooklm").length
      ? sourceTypes.filter((t) => t !== "local" && t !== "notebooklm")
      : (["patents", "literature", "internet"] as SearchSourceType[])
  );
  const [urlOpen, setUrlOpen] = useState(false);
  const [urlValue, setUrlValue] = useState("");
  const [textOpen, setTextOpen] = useState(false);
  const [textTitle, setTextTitle] = useState("");
  const [textBody, setTextBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  function toggleType(t: SearchSourceType) {
    setModalTypes((prev) =>
      prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]
    );
  }

  async function runSearch() {
    setError(null);
    setSourceTypes(modalTypes);
    await searchSources(keyword);
  }

  async function addUrl() {
    if (!urlValue.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.ingestUrl(urlValue.trim());
      addSources(res.evidence);
      setUrlOpen(false);
      setUrlValue("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function addText() {
    if (!textBody.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.ingestText(textBody.trim(), textTitle.trim() || "Pasted text");
      addSources(res.evidence);
      setTextOpen(false);
      setTextBody("");
      setTextTitle("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Modal title="添加数据源 · Add sources" open={open} onClose={onClose} size="lg">
        <div className="space-y-4">
          <label className="block">
            <span className="text-xs text-slate-400">关键词 · Search the web for new sources</span>
            <div className="mt-1 flex gap-2">
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="输入检索关键词…"
                className="flex-1 bg-ink border border-accent/40 rounded-lg px-3 py-2 text-sm outline-none"
              />
              <button
                type="button"
                disabled={searchBusy || modalTypes.length === 0}
                onClick={() => void runSearch()}
                className="shrink-0 bg-accent/90 hover:bg-accent text-ink font-semibold rounded-lg px-4 text-sm disabled:opacity-40"
              >
                {searchBusy ? "检索中…" : "检索"}
              </button>
            </div>
          </label>

          <div className="flex flex-wrap gap-2">
            {MODAL_SOURCE_TYPES.map((t) => {
              const on = modalTypes.includes(t.id);
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => toggleType(t.id)}
                  className={`text-xs rounded-full px-3 py-1 border transition-colors ${
                    on ? "border-accent/50 bg-accent/10 text-accent" : "border-edge text-slate-400"
                  }`}
                >
                  {t.icon} {t.label}
                </button>
              );
            })}
          </div>

          <div
            className="border border-dashed border-edge rounded-xl p-6 text-center bg-ink/40"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const files = Array.from(e.dataTransfer.files);
              if (files.length) void uploadFiles(files);
            }}
          >
            <p className="text-sm text-slate-300 mb-1">或拖拽文件到此处</p>
            <p className="text-[11px] text-slate-500 mb-4">pdf, images, docs, audio, and more</p>
            <div className="flex flex-wrap justify-center gap-2">
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
                type="button"
                onClick={() => fileInput.current?.click()}
                className="text-xs border border-edge rounded-full px-3 py-1.5 text-slate-300 hover:border-accent/40 hover:text-accent"
              >
                ⬆ 上传文件
              </button>
              <button
                type="button"
                onClick={() => setUrlOpen(true)}
                className="text-xs border border-edge rounded-full px-3 py-1.5 text-slate-300 hover:border-accent/40 hover:text-accent"
              >
                🔗 添加网页
              </button>
              <button
                type="button"
                onClick={() => setTextOpen(true)}
                className="text-xs border border-edge rounded-full px-3 py-1.5 text-slate-300 hover:border-accent/40 hover:text-accent"
              >
                📋 复制文本
              </button>
            </div>
          </div>

          {error && (
            <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded p-2">
              {error}
            </div>
          )}

          <div className="text-[10px] text-slate-500 space-y-1">
            <div className="flex justify-between">
              <span>已加载资料</span>
              <span className="font-mono">
                {sources.length} / {SOURCE_LIMIT} · 已选 {selectedSources.length}
              </span>
            </div>
            <div className="h-1.5 bg-edge rounded overflow-hidden">
              <div
                className="h-full bg-accent transition-all"
                style={{ width: `${Math.min(100, (sources.length / SOURCE_LIMIT) * 100)}%` }}
              />
            </div>
          </div>
        </div>
      </Modal>

      <Modal title="添加网页 URL" open={urlOpen} onClose={() => setUrlOpen(false)} nested size="md">
        <div className="space-y-3">
          <input
            value={urlValue}
            onChange={(e) => setUrlValue(e.target.value)}
            placeholder="https://..."
            className="w-full bg-ink border border-edge rounded px-3 py-2 text-sm font-mono"
          />
          <button
            type="button"
            disabled={busy || !urlValue.trim()}
            onClick={() => void addUrl()}
            className="w-full bg-accent/90 hover:bg-accent text-ink font-semibold rounded py-2 text-sm disabled:opacity-40"
          >
            {busy ? "抓取中…" : "确定添加"}
          </button>
        </div>
      </Modal>

      <Modal title="粘贴文本" open={textOpen} onClose={() => setTextOpen(false)} nested size="md">
        <div className="space-y-3">
          <input
            value={textTitle}
            onChange={(e) => setTextTitle(e.target.value)}
            placeholder="标题（可选）"
            className="w-full bg-ink border border-edge rounded px-3 py-2 text-sm"
          />
          <textarea
            value={textBody}
            onChange={(e) => setTextBody(e.target.value)}
            rows={8}
            placeholder="粘贴文本内容…"
            className="w-full bg-ink border border-edge rounded px-3 py-2 text-sm resize-none"
          />
          <button
            type="button"
            disabled={busy || !textBody.trim()}
            onClick={() => void addText()}
            className="w-full bg-accent/90 hover:bg-accent text-ink font-semibold rounded py-2 text-sm disabled:opacity-40"
          >
            {busy ? "添加中…" : "确定添加"}
          </button>
        </div>
      </Modal>
    </>
  );
}
