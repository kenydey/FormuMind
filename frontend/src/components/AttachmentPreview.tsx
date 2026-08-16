import { useCallback, useEffect, useRef, useState } from "react";
import { api, formatApiError, type Attachment } from "../api";

interface AttachmentPreviewProps {
  experimentId: number;
  onClose: () => void;
  onChanged?: (count: number) => void;
}

/**
 * Attachment list + upload overlay for one experiment (Phase 2.1).
 *
 * Files are stored in Datalab ELN via POST /experiments/{id}/attachments and
 * surfaced here as a traceable list. Upload is best-effort: on failure the
 * error is shown inline and the list is left untouched.
 */
export default function AttachmentPreview({
  experimentId,
  onClose,
  onChanged,
}: AttachmentPreviewProps) {
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api
      .getAttachments(experimentId)
      .then((rows) => {
        setAttachments(rows);
        onChanged?.(rows.length);
      })
      .catch((e) => setError(formatApiError(e)));
  }, [experimentId, onChanged]);

  useEffect(() => {
    load();
  }, [load]);

  async function upload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      await api.uploadAttachment(file, experimentId);
      await load();
      if (fileRef.current) fileRef.current.value = "";
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-edge rounded-lg shadow-xl w-[30rem] max-w-[92vw] p-4 text-sm"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-slate-200">
            实验附件（#{experimentId}）
          </h3>
          <button
            className="text-slate-400 hover:text-slate-200"
            onClick={onClose}
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        <div className="flex items-center gap-2 mb-3">
          <input
            ref={fileRef}
            type="file"
            className="flex-1 text-xs text-slate-400 file:mr-2 file:px-2 file:py-1
                       file:rounded file:border file:border-edge file:bg-panel file:text-slate-300"
          />
          <button
            className="px-3 py-1.5 rounded bg-accent/20 border border-accent/40 text-accent
                       hover:bg-accent/30 disabled:opacity-50"
            onClick={upload}
            disabled={busy}
          >
            {busy ? "上传中…" : "📎 上传"}
          </button>
        </div>

        {error && (
          <div className="text-red-400 bg-red-400/10 border border-red-400/20 rounded p-2 mb-2 text-xs">
            {error}
          </div>
        )}

        {attachments.length === 0 ? (
          <p className="text-slate-500 text-xs">暂无附件</p>
        ) : (
          <ul className="space-y-1.5 max-h-64 overflow-y-auto">
            {attachments.map((a) => (
              <li
                key={a.id}
                className="flex items-center gap-2 text-xs border border-edge/50 rounded px-2 py-1.5 bg-ink/20"
              >
                <span>📎</span>
                <span className="flex-1 truncate text-slate-300">
                  {a.filename || a.source_document_id}
                </span>
                <span className="text-slate-500">{a.kind}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
