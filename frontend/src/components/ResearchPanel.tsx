import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../store";
import type { Evidence, StructureRecognitionResult } from "../api";
import { api } from "../api";
import type { NotificationKind } from "../store/notifications";
import MarkdownMessage from "./MarkdownMessage";
import NotificationStack from "./NotificationStack";

/**
 * Must stay in step with the stages `research_graph._emit` actually sends.
 * `regenerate` was missing, so `stageIndex` fell through to 0 and the progress
 * bar jumped back to 检索 every time a report was rewritten — the run looked
 * like it had restarted. `recommend` is emitted only by the recommend-mode
 * pipeline, so deep research legitimately ends at 核验/修正.
 */
const CRAG_STAGES = [
  { id: "retrieve", label: "检索" },
  { id: "grade", label: "评估" },
  { id: "fallback", label: "补搜" },
  { id: "generate", label: "生成" },
  { id: "claim_check", label: "核验" },
  { id: "regenerate", label: "修正" },
] as const;

export const CRAG_STAGE_IDS: readonly string[] = CRAG_STAGES.map((s) => s.id);

function stageIndex(stage: string): number {
  const idx = CRAG_STAGE_IDS.indexOf(stage);
  // 0 covers the pre-start case, where `deepResearchStage` is "". It is also
  // what an unrecognised stage falls back to, which is why the list above being
  // complete is the actual guarantee — `backend deep-mode stages ⊆ CRAG_STAGE_IDS`
  // is asserted on both sides of the boundary.
  return idx >= 0 ? idx : 0;
}

/**
 * The six CRAG stages, rendered as the deep-research notification's detail.
 *
 * This stays in ResearchPanel rather than moving into NotificationStack: the
 * stage list is chat-panel domain knowledge, and flattening it to the stack's
 * scalar `progress` would lose the per-stage labels.
 */
function DeepResearchStages({
  activeStageIdx,
  progressPct,
}: {
  activeStageIdx: number;
  progressPct: number;
}) {
  return (
    <>
      <div className="flex gap-1 mb-2">
        {CRAG_STAGES.map((s, i) => {
          const done = i < activeStageIdx;
          const active = i === activeStageIdx;
          return (
            <div key={s.id} className="flex-1 min-w-0">
              <div
                className={`h-1 rounded-full transition-colors ${
                  done ? "bg-accent" : active ? "bg-accent/70 animate-pulse" : "bg-edge"
                }`}
              />
              <div
                className={`mt-1 text-[9px] text-center truncate ${
                  active ? "text-accent font-semibold" : done ? "text-slate-400" : "text-slate-600"
                }`}
              >
                {s.label}
              </div>
            </div>
          );
        })}
      </div>
      <div className="h-1 bg-edge rounded overflow-hidden">
        <div
          className="h-full bg-accent/80 transition-all duration-500"
          style={{ width: `${Math.min(100, progressPct)}%` }}
        />
      </div>
    </>
  );
}

function CitationChip({ ev }: { ev: Evidence }) {
  return (
    <span
      className="inline-flex items-center gap-1 bg-accent/10 border border-accent/30 text-accent rounded px-1.5 py-0.5 text-[10px] mr-1 mb-1"
      title={ev.snippet}
    >
      {ev.is_seed_corpus && (
        <span className="text-amber-400/90 shrink-0" title="离线示例摘要">
          示例
        </span>
      )}
      <span className="truncate max-w-[140px]">{ev.title}</span>
    </span>
  );
}

export default function ResearchPanel() {
  const {
    chatHistory,
    chatBusy,
    sendChat,
    sources,
    selectedSources,
    deepResearchBusy,
    deepResearchStage,
    task,
    chatSessions,
    chatSessionsOpen,
    activeSessionId,
    chatSessionTitles,
    chatSessionsBusy,
    setChatSessionsOpen,
    refreshChatSessions,
    newChatSession,
    switchChatSession,
    deleteChatSession,
  } = useStore(
    useShallow((s) => ({
      chatHistory: s.chatHistory,
      chatBusy: s.chatBusy,
      sendChat: s.sendChat,
      sources: s.sources,
      selectedSources: s.selectedSources,
      deepResearchBusy: s.deepResearchBusy,
      deepResearchStage: s.deepResearchStage,
      task: s.task,
      chatSessions: s.chatSessions,
      chatSessionsOpen: s.chatSessionsOpen,
      activeSessionId: s.activeSessionId,
      chatSessionTitles: s.chatSessionTitles,
      chatSessionsBusy: s.chatSessionsBusy,
      setChatSessionsOpen: s.setChatSessionsOpen,
      refreshChatSessions: s.refreshChatSessions,
      newChatSession: s.newChatSession,
      switchChatSession: s.switchChatSession,
      deleteChatSession: s.deleteChatSession,
    }))
  );
  const [draft, setDraft] = useState("");
  const [structInfo, setStructInfo] = useState<StructureRecognitionResult | null>(null);
  const [structBusy, setStructBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  async function uploadStructure(file: File) {
    setStructBusy(true);
    try {
      const res = await api.uploadStructure(file);
      setStructInfo(res);
    } catch (e) {
      setStructInfo({
        recognized: false,
        smiles: null,
        moljson: null,
        hits: [],
        image_sha: "",
        cached: false,
        warnings: [(e as Error).message || "结构图上传失败"],
        error: String(e),
      });
    } finally {
      setStructBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function clearStructure() {
    setStructInfo(null);
  }

  const activeStageIdx = stageIndex(deepResearchStage);
  // `task` is a single slot written by recommend, optimize and loop as well, so
  // its progress is only ours when the kind matches; otherwise fall back to the
  // stage index.
  const taskProgress = task?.kind === "deep_research" ? (task.progress ?? 0) : 0;
  const progressPct = deepResearchBusy
    ? Math.round((taskProgress || (activeStageIdx + 1) / CRAG_STAGES.length) * 100)
    : 0;

  function renderNotificationDetail(kind: NotificationKind) {
    if (kind !== "deep-research") return undefined;
    return <DeepResearchStages activeStageIdx={activeStageIdx} progressPct={progressPct} />;
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [chatHistory, chatBusy]);

  const selectedCount = selectedSources.length;
  const canSend = selectedCount > 0 && !chatBusy;

  function submit() {
    const q = draft.trim();
    if (!q || !canSend) return;
    setDraft("");
    sendChat(q, structInfo);
    // 结构上下文随本次提问消费后清空，避免串到下一问。
    setStructInfo(null);
  }

  return (
    <section className="glass rounded-xl flex flex-col h-full overflow-hidden">
      <div className="px-4 py-3 border-b border-edge shrink-0">
        <div className="flex items-center justify-between">
          <h2 className="text-sm uppercase tracking-widest text-accent2">研究 · Research</h2>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => {
                const next = !chatSessionsOpen;
                setChatSessionsOpen(next);
                if (next) void refreshChatSessions();
              }}
              className={`text-[10px] rounded-full border px-2 py-0.5 transition-colors ${
                chatSessionsOpen || activeSessionId
                  ? "border-accent/50 bg-accent/10 text-accent"
                  : "border-edge text-slate-400 hover:text-accent hover:border-accent/40"
              }`}
              title="会话存档: 新建/切换/恢复对话线程(后端 Redis 持久)"
            >
              💬 会话{activeSessionId ? " · " + ((chatSessionTitles[activeSessionId] ?? "").slice(0, 8) || "…") : ""}
            </button>
            <button
              type="button"
              disabled={chatBusy}
              onClick={() => void newChatSession()}
              className="text-[10px] rounded-full border border-edge px-2 py-0.5 text-slate-400 hover:border-accent/40 hover:text-accent disabled:opacity-40"
              title="开始新会话(当前对话自动存档)"
            >
              ＋ 新建
            </button>
          </div>
        </div>
        <p className="text-[11px] text-slate-500 mt-0.5">
          基于左栏已选的 {selectedCount} / {sources.length} 条资料进行问答（RAG 接地）
        </p>
      </div>

      {chatSessionsOpen && (
        <div className="shrink-0 border-b border-edge bg-ink/40 px-3 py-2">
          {chatSessionsBusy ? (
            <div className="text-[11px] text-slate-500 py-1">会话列表加载中…</div>
          ) : (
            <div className="space-y-1 max-h-40 overflow-auto">
              {chatSessions.length === 0 && (
                <div className="text-[11px] text-slate-500 py-1">
                  暂无存档会话 — 每轮问答完成后自动存档; 点「＋ 新建」可开新线程。
                </div>
              )}
              {chatSessions.map((s) => {
                const title = s.title || chatSessionTitles[s.session_id] || s.session_id.slice(0, 12);
                const active = s.session_id === activeSessionId;
                return (
                  <div
                    key={s.session_id}
                    className={`flex items-center gap-2 rounded px-2 py-1 text-[11px] ${
                      active ? "bg-accent/15 text-accent" : "text-slate-300 hover:bg-ink/80"
                    }`}
                  >
                    <button
                      type="button"
                      disabled={active || chatBusy}
                      onClick={() => void switchChatSession(s.session_id)}
                      className="flex-1 text-left truncate disabled:opacity-60"
                      title={s.session_id}
                    >
                      {active ? "▶ " : ""}
                      {title}
                      <span className="text-slate-600 ml-1">({s.history_count ?? 0} 轮)</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => void deleteChatSession(s.session_id)}
                      className="text-slate-600 hover:text-rose-400"
                      title="删除该存档(当前对话不受影响)"
                    >
                      ✕
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* All run-status notifications — search, KB build, deep research,
          errors — consolidated here from the left column. */}
      <NotificationStack renderDetail={renderNotificationDetail} />

      {/* Conversation */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0 p-4 flex flex-col gap-3">
        {chatHistory.length === 0 ? (
          <div className="text-slate-600 text-sm m-auto text-center max-w-sm">
            {sources.length === 0
              ? "先在左栏检索或上传资料，然后在此向资料提问。"
              : selectedCount === 0
                ? "请在左栏勾选至少一条资料用于问答。"
                : "资料已就绪。在下方输入问题，例如「这些专利的主要防腐机理是什么？」"}
          </div>
        ) : (
          chatHistory.map((m, i) => (
            <div
              key={i}
              className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                  m.role === "user"
                    ? "bg-accent/15 border border-accent/30 text-slate-200"
                    : "bg-ink/60 border border-edge text-slate-300"
                }`}
              >
                {m.role === "assistant" ? (
                  m.streaming ? (
                    <div className="whitespace-pre-wrap leading-relaxed font-mono text-[13px] text-slate-300">
                      {m.content}
                      {m.phase === "retrieval" ? (
                        <span className="text-accent2 text-xs ml-1">
                          ⏳ 检索资料中…
                        </span>
                      ) : m.phase === "answering" ? (
                        <span className="inline-block w-2 h-4 bg-accent2/80 ml-0.5 animate-pulse align-middle" />
                      ) : m.phase === "claims" ? (
                        <span className="text-slate-500 text-xs ml-1">
                          ✓ 核对引用…
                        </span>
                      ) : null}
                    </div>
                  ) : (
                    <MarkdownMessage content={m.content} />
                  )
                ) : (
                  <div className="whitespace-pre-wrap leading-relaxed">{m.content}</div>
                )}
                {m.role === "assistant" && (m.kbChunksUsed ?? 0) > 0 && (
                  <div className="mt-1.5">
                    <span
                      className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-300"
                      title="本回答额外融合了持久知识库中的全文片段"
                    >
                      知识库 ×{m.kbChunksUsed}
                    </span>
                  </div>
                )}
                {m.citations && m.citations.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-edge/60 flex flex-wrap">
                    {m.citations.map((c, j) => (
                      <CitationChip key={j} ev={c} />
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))
        )}
        {chatBusy && (
          <div className="flex justify-start">
            <div className="bg-ink/60 border border-edge rounded-lg px-3 py-2 text-sm text-slate-500">
              思考中…
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="p-3 border-t border-edge shrink-0">
        {/* 结构图识别结果预览（可清除） */}
        {structInfo && (
          <div className="mb-2 rounded bg-ink/50 border border-accent/30 px-2.5 py-1.5 text-[11px]">
            {structInfo.recognized ? (
              <div className="text-slate-300">
                <span className="text-emerald-300 font-semibold">✓ 已识别结构</span>
                <code className="ml-1.5 text-accent">{structInfo.smiles}</code>
                {structInfo.confidence != null && structInfo.confidence < 0.6 && (
                  <span
                    className="ml-1.5 inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-300"
                    title="MolScribe 识别置信度偏低，建议人工核对结构"
                  >
                    低置信 {Math.round(structInfo.confidence * 100)}%
                  </span>
                )}
                {structInfo.hits.length > 0 && (
                  <div className="mt-1 text-slate-400">
                    相似材料：{structInfo.hits.map((h) => `${h.name} (${Math.round(h.similarity * 100)}%)`).join("、")}
                  </div>
                )}
              </div>
            ) : (
              <div className="text-amber-300/90">
                ⚠ 未能识别：{structInfo.error || "未知错误"}
                {structInfo.warnings.length > 0 && (
                  <div className="text-slate-400">{structInfo.warnings.join("；")}</div>
                )}
              </div>
            )}
            <button
              onClick={clearStructure}
              className="mt-1 text-[10px] text-slate-500 hover:text-slate-300"
            >
              ✕ 移除结构上下文
            </button>
          </div>
        )}
        <div className="flex gap-2 items-end">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) uploadStructure(f);
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={structBusy || chatBusy}
            title="上传化学结构式图片（MolScribe 识别 → 相似材料检索）"
            className="bg-ink border border-edge hover:border-accent/50 rounded px-2.5 py-1.5 text-sm shrink-0 disabled:opacity-50"
          >
            {structBusy ? "识别中…" : "📷"}
          </button>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            disabled={!canSend}
            placeholder={
              sources.length === 0
                ? "请先加载资料…"
                : selectedCount === 0
                  ? "请先勾选资料…"
                  : "向资料提问…（Enter 发送，Shift+Enter 换行）"
            }
            className="flex-1 bg-ink border border-edge rounded px-2.5 py-1.5 text-sm resize-none focus:border-accent/50 outline-none disabled:opacity-50"
          />
          <button
            onClick={submit}
            disabled={!canSend || !draft.trim()}
            className="bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-4 py-2 text-sm disabled:opacity-40 shrink-0"
          >
            发送
          </button>
        </div>
      </div>
    </section>
  );
}
