import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import { api, type SearchSourceType, type NotebookLMStatus, type NotebookLMLoginResult } from "../api";
import AddSourceModal from "./AddSourceModal";

const SOURCE_TYPES: { id: SearchSourceType; label: string; icon: string }[] = [
  { id: "patents", label: "专利 Patents", icon: "📄" },
  { id: "literature", label: "文献 Literature", icon: "📚" },
  { id: "internet", label: "互联网 Internet", icon: "🌐" },
  { id: "local", label: "本地文件 Local", icon: "📎" },
  { id: "notebooklm", label: "NotebookLM", icon: "📓" },
];

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
    runDeepResearch,
    deepResearchBusy,
    error,
  } = useStore();
  const fileInput = useRef<HTMLInputElement>(null);
  const [addSourceOpen, setAddSourceOpen] = useState(false);

  // Load source availability on mount so badges appear before first search
  useEffect(() => {
    loadSourceStatus();
  }, [loadSourceStatus]);

  function toggleType(t: SearchSourceType) {
    if (sourceTypes.includes(t)) {
      setSourceTypes(sourceTypes.filter((x) => x !== t));
    } else {
      setSourceTypes([...sourceTypes, t]);
    }
  }

  const localSelected = sourceTypes.includes("local");
  const notebooklmSelected = sourceTypes.includes("notebooklm");

  // NotebookLM authorization panel (local state; backend has no OAuth-URL API —
  // it spawns `notebooklm login` on the backend machine, or guides manual login).
  const [nbStatus, setNbStatus] = useState<NotebookLMStatus | null>(null);
  const [nbId, setNbId] = useState("");
  const [nbBusy, setNbBusy] = useState(false);
  const [nbLogin, setNbLogin] = useState<NotebookLMLoginResult | null>(null);

  async function refreshNbStatus() {
    try {
      const st = await api.notebooklmStatus();
      setNbStatus(st);
      if (st.notebook_id && !nbId) setNbId(st.notebook_id);
      return st;
    } catch {
      return null;
    }
  }

  // Fetch detailed NotebookLM status whenever the source is selected.
  useEffect(() => {
    if (notebooklmSelected) refreshNbStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notebooklmSelected]);

  async function nbAuthorize() {
    setNbBusy(true);
    setNbLogin(null);
    try {
      // Persist enable + notebook id at runtime, then trigger login.
      await api.notebooklmConfig({ enabled: true, notebook_id: nbId.trim() || undefined });
      const res = await api.notebooklmLogin();
      setNbLogin(res);
      // Poll status for a while so the badge flips to ready once the session lands.
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const st = await refreshNbStatus();
        if (st?.available || st?.session_present) break;
      }
      loadSourceStatus();
    } catch {
      /* surfaced via status panel */
    } finally {
      setNbBusy(false);
    }
  }

  const chemcrowStatus = sourceStatus["chemcrow"];
  const chemcrowRelevant =
    sourceTypes.includes("literature") || sourceTypes.includes("internet");
  const showChemcrowBadge = chemcrowRelevant && chemcrowStatus !== undefined;

  function statusDot(id: SearchSourceType) {
    const st = sourceStatus[id];
    if (!st) return null;
    if (st.available) {
      return <span className="w-1.5 h-1.5 rounded-full bg-green-400 shrink-0" title="可用" />;
    }
    if (st.offline_fallback) {
      return (
        <span
          className="w-1.5 h-1.5 rounded-full bg-yellow-400 shrink-0"
          title={st.hint ?? "仅离线模式"}
        />
      );
    }
    return (
      <span
        className="w-1.5 h-1.5 rounded-full bg-red-400/70 shrink-0"
        title={st.hint ?? "来源不可用"}
      />
    );
  }

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
              <span className="truncate flex-1">{t.label}</span>
              {statusDot(t.id)}
            </button>
          );
        })}
      </div>

      {/* NotebookLM authorization panel */}
      {notebooklmSelected && nbStatus && (
        nbStatus.available ? (
          <div className="shrink-0 text-xs bg-emerald-500/10 border border-emerald-500/20 rounded p-2 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
            <span className="text-emerald-300 font-medium">📓 NotebookLM 已就绪</span>
            {nbStatus.notebook_id && (
              <span className="text-slate-500 truncate" title={nbStatus.notebook_id}>
                · {nbStatus.notebook_id}
              </span>
            )}
          </div>
        ) : (
          <div className="shrink-0 text-xs bg-yellow-400/10 border border-yellow-400/20 rounded p-2 flex flex-col gap-1.5">
            <span className="text-yellow-300 font-medium">📓 NotebookLM 授权</span>

            {nbStatus.lib_installed === false ? (
              <>
                <span className="text-slate-400 leading-relaxed">
                  需先安装 notebooklm-py 依赖库。
                </span>
                <button
                  onClick={() => openSettings("deps")}
                  className="self-start text-[11px] border border-yellow-400/40 text-yellow-200 rounded px-2 py-1 hover:bg-yellow-400/15"
                >
                  去依赖管理安装 →
                </button>
              </>
            ) : (
              <>
                <label className="block">
                  <span className="text-slate-400">Notebook ID</span>
                  <input
                    value={nbId}
                    onChange={(e) => setNbId(e.target.value)}
                    placeholder="NotebookLM 笔记本链接中的 ID"
                    className="w-full mt-0.5 bg-ink border border-edge rounded px-2 py-1 text-[11px] font-mono focus:border-accent/50 outline-none"
                  />
                </label>
                <button
                  onClick={nbAuthorize}
                  disabled={nbBusy}
                  className="self-start text-[11px] bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-2.5 py-1 disabled:opacity-40"
                >
                  {nbBusy ? "授权中…" : "授权登录 Google"}
                </button>

                {/* Login result: browser launched, or manual fallback */}
                {nbLogin && (
                  <div className="text-slate-400 leading-relaxed">
                    {nbLogin.hint}
                    {nbLogin.mode === "manual" && (
                      <div className="mt-1 flex flex-col gap-1">
                        {nbLogin.command && (
                          <code className="text-[10px] text-slate-300 bg-ink/60 rounded px-1 py-0.5 block break-all">
                            {nbLogin.command}
                          </code>
                        )}
                        <a
                          href={nbLogin.manual_url ?? "https://notebooklm.google.com"}
                          target="_blank"
                          rel="noreferrer"
                          className="text-accent hover:underline"
                        >
                          打开 NotebookLM →
                        </a>
                      </div>
                    )}
                  </div>
                )}
                {!nbLogin && nbStatus.hint && (
                  <span className="text-slate-500 leading-relaxed">{nbStatus.hint}</span>
                )}
              </>
            )}
          </div>
        )
      )}

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

      {/* Unavailable source summary (after first search) */}
      {Object.keys(sourceStatus).length > 0 && (
        (() => {
          const unavail = SOURCE_TYPES.filter((t) => {
            const st = sourceStatus[t.id];
            return st && !st.available && !st.offline_fallback && t.id !== "notebooklm";
          });
          if (unavail.length === 0) return null;
          const hint = sourceStatus[unavail[0].id]?.hint;
          return (
            <div className="shrink-0 text-[11px] bg-slate-800/60 border border-edge rounded p-2 text-slate-400">
              <span className="text-slate-300">
                {unavail.map((t) => t.icon).join(" ")} 库未安装
              </span>
              {hint && (
                <div className="mt-0.5 text-slate-500 truncate" title={hint}>
                  {hint}
                </div>
              )}
            </div>
          );
        })()
      )}

      {/* Upload + search */}
      <div className="flex gap-2 shrink-0">
        {localSelected && (
          <>
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
              title="上传本地文件"
            >
              ⬆ 上传
            </button>
          </>
        )}
        <button
          onClick={() => void searchSources()}
          disabled={searchBusy || sourceTypes.length === 0}
          className="flex-1 bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-1.5 text-sm disabled:opacity-40"
        >
          {searchBusy ? "检索中…" : "开始检索"}
        </button>
      </div>

      {/* Deep research: async multi-agent knowledge cohort */}
      <button
        onClick={runDeepResearch}
        disabled={deepResearchBusy || searchBusy}
        className="shrink-0 w-full border border-accent2/40 bg-accent2/10 hover:bg-accent2/20 text-accent2 font-semibold rounded px-3 py-1.5 text-sm disabled:opacity-40 flex items-center justify-center gap-1.5"
        title="多源检索 + QueryExpander 扩展 + RAG 重排 + 引用报告"
      >
        {deepResearchBusy ? "🔬 深度研究中…" : "🔬 深度研究"}
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
            勾选信息类别并填写主题后点击「开始检索」，或上传本地文件。检索完成后即可在中栏向资料提问。
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
