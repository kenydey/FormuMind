import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../store";
import type { Evidence } from "../api";

const CRAG_STAGES = [
  { id: "retrieve", label: "检索" },
  { id: "grade", label: "评估" },
  { id: "fallback", label: "补搜" },
  { id: "generate", label: "生成" },
  { id: "claim_check", label: "核验" },
  { id: "recommend", label: "推荐" },
] as const;

function stageIndex(stage: string): number {
  const idx = CRAG_STAGES.findIndex((s) => s.id === stage);
  return idx >= 0 ? idx : 0;
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
    deepReport,
    deepResearchBusy,
    deepResearchStage,
    deepResearchMessage,
    task,
  } = useStore(
    useShallow((s) => ({
      chatHistory: s.chatHistory,
      chatBusy: s.chatBusy,
      sendChat: s.sendChat,
      sources: s.sources,
      selectedSources: s.selectedSources,
      deepReport: s.deepReport,
      deepResearchBusy: s.deepResearchBusy,
      deepResearchStage: s.deepResearchStage,
      deepResearchMessage: s.deepResearchMessage,
      task: s.task,
    }))
  );
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const activeStageIdx = stageIndex(deepResearchStage);
  const progressPct = deepResearchBusy
    ? Math.round(((task?.progress ?? 0) || (activeStageIdx + 1) / CRAG_STAGES.length) * 100)
    : 0;

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [chatHistory, chatBusy]);

  const selectedCount = selectedSources.length;
  const canSend = selectedCount > 0 && !chatBusy;

  function submit() {
    const q = draft.trim();
    if (!q || !canSend) return;
    setDraft("");
    sendChat(q);
  }

  return (
    <section className="glass rounded-xl flex flex-col h-full overflow-hidden">
      <div className="px-4 py-3 border-b border-edge shrink-0">
        <h2 className="text-sm uppercase tracking-widest text-accent2">研究 · Research</h2>
        <p className="text-[11px] text-slate-500 mt-0.5">
          基于左栏已选的 {selectedCount} / {sources.length} 条资料进行问答（RAG 接地）
        </p>
      </div>

      {deepResearchBusy && (
        <div className="mx-4 mt-3 mb-1 rounded-lg border border-accent/30 bg-accent/5 px-3 py-2.5 shrink-0">
          <div className="flex items-center justify-between text-[11px] text-slate-400 mb-2">
            <span className="text-accent2 uppercase tracking-widest">深度研究 · CRAG</span>
            <span>{deepResearchMessage || "处理中…"}</span>
          </div>
          <div className="flex gap-1 mb-2">
            {CRAG_STAGES.map((s, i) => {
              const done = i < activeStageIdx;
              const active = i === activeStageIdx;
              return (
                <div key={s.id} className="flex-1 min-w-0">
                  <div
                    className={`h-1 rounded-full transition-colors ${
                      done
                        ? "bg-accent"
                        : active
                          ? "bg-accent/70 animate-pulse"
                          : "bg-edge"
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
        </div>
      )}

      {deepReport && !deepResearchBusy && (
        <div className="mx-4 mt-3 mb-1 rounded-lg border border-violet-500/30 bg-violet-500/5 px-3 py-2 text-[11px] text-violet-200 shrink-0">
          深度研究报告已生成（{deepReport.citations?.length ?? 0} 条引用）— 见下方对话与左栏资料列表
        </div>
      )}

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
                <div className="whitespace-pre-wrap leading-relaxed">{m.content}</div>
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
        <div className="flex gap-2 items-end">
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
