import { useCallback, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, formatApiError, type ChatMessage, type Evidence } from "../api";
import { useStore } from "../store";

type Props = {
  message: ChatMessage;
  /** Preceding user question for draft title/body. */
  question?: string;
  origin?: "chat" | "deep_research";
};

/**
 * S4: persist assistant answer as L2 ``queries/`` draft (unreviewed; not Claims).
 * Flag-gated via ``wiki_chat_save_draft``.
 */
export default function SaveWikiDraftButton({
  message,
  question = "",
  origin = "chat",
}: Props) {
  const { activeProjectId, openSettings } = useStore(
    useShallow((s) => ({
      activeProjectId: s.activeProjectId,
      openSettings: s.openSettings,
    })),
  );
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const onSave = useCallback(async () => {
    if (!activeProjectId) {
      setErr("请先选择活动项目");
      return;
    }
    const answer = (message.content || "").trim();
    if (!answer) {
      setErr("回答为空");
      return;
    }
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const citations = (message.citations || []).map((c: Evidence) => ({
        title: c.title,
        source: c.source,
        identifier: c.identifier,
        snippet: c.snippet,
      }));
      const out = await api.saveWikiDraft({
        project_id: activeProjectId,
        question: question || "",
        answer_markdown: answer,
        citations,
        origin,
      });
      setMsg(`已存草稿 ${out.path} · ${out.disclaimer || "draft_not_claims"}`);
    } catch (e) {
      const text = formatApiError(e);
      setErr(text);
      if (/wiki_chat_save_draft/i.test(text) || /未启用|is false/i.test(text)) {
        // leave CTA via err + open settings hint below
      }
    } finally {
      setBusy(false);
    }
  }, [activeProjectId, message.content, message.citations, origin, question]);

  const needsFlag =
    !!err && (/wiki_chat_save_draft/i.test(err) || /is false/i.test(err));

  return (
    <div className="mt-1.5 flex flex-col gap-1" data-testid="save-wiki-draft">
      <div className="flex flex-wrap items-center gap-1">
        <button
          type="button"
          className="text-[10px] px-1.5 py-0.5 rounded border border-violet-500/40 text-violet-200 hover:bg-violet-500/15 disabled:opacity-40"
          disabled={busy || !message.content?.trim()}
          title="写入 queries/ L2 草稿（unreviewed；不进 Claims/DOE）"
          data-testid="save-wiki-draft-btn"
          onClick={() => void onSave()}
        >
          {busy ? "保存中…" : "存为 Wiki 草稿"}
        </button>
        {needsFlag && (
          <button
            type="button"
            className="text-[10px] px-1.5 py-0.5 rounded border border-amber-500/40 text-amber-200"
            data-testid="save-wiki-draft-open-env"
            onClick={() => openSettings("env", { focusEnvAttr: "wiki_chat_save_draft" })}
          >
            去设置开启
          </button>
        )}
      </div>
      {msg && (
        <div className="text-[10px] text-emerald-300/90" data-testid="save-wiki-draft-ok">
          {msg}
        </div>
      )}
      {err && (
        <div className="text-[10px] text-rose-300/90" data-testid="save-wiki-draft-err">
          {err}
        </div>
      )}
    </div>
  );
}
