import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import type { Evidence } from "../api";

function CitationChip({ ev }: { ev: Evidence }) {
  return (
    <span
      className="inline-flex items-center gap-1 bg-accent/10 border border-accent/30 text-accent rounded px-1.5 py-0.5 text-[10px] mr-1 mb-1"
      title={ev.snippet}
    >
      <span className="truncate max-w-[140px]">{ev.title}</span>
    </span>
  );
}

export default function ResearchPanel() {
  const { chatHistory, chatBusy, sendChat, sources, selectedSources } = useStore();
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

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
