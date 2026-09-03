import { useCallback, useEffect, useRef, useState } from "react";
import { api, formatApiError, type Attachment } from "../api";

interface AttachmentPreviewProps {
  campaignId: number;
  rowId: number;
  onClose: () => void;
  onChanged?: (count: number) => void;
}

/**
 * Attachment list + upload overlay for one workbench row (Phase 2.1).
 *
 * The backend resolves the row to its training experiment via the ``label``
 * bridge, so files are bound to the correct experiment — not the raw row id.
 * Upload is best-effort: on failure the error is shown inline.
 */
export default function AttachmentPreview({
  campaignId,
  rowId,
  onClose,
  onChanged,
}: AttachmentPreviewProps) {
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api
      .getWorkbenchAttachments(campaignId, rowId)
      .then((rows) => {
        setAttachments(rows);
        onChanged?.(rows.length);
      })
      .catch((e) => setError(formatApiError(e)));
  }, [campaignId, rowId, onChanged]);

  useEffect(() => {
    load();
  }, [load]);

  async function upload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      await api.uploadWorkbenchAttachment(file, campaignId, rowId);
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
          <h3 className="font-semibold text-slate-200">实验附件（行 #{rowId}）</h3>
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
                <a
                  href={api.workbenchAttachmentDownloadUrl(campaignId, rowId, a.id)}
                  download={a.filename || undefined}
                  className="text-accent hover:text-accent/80"
                  title="下载原件（DataLab 归档副本优先）"
                >
                  ⬇ 下载
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
