import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

export type ModalSize = "md" | "lg" | "xl" | "full";

const SIZE_CLASS: Record<ModalSize, string> = {
  md: "max-w-2xl",
  lg: "max-w-4xl",
  xl: "max-w-6xl",
  full: "max-w-[min(96vw,1400px)]",
};

export default function Modal({
  title,
  open,
  onClose,
  children,
  size,
  wide,
  nested,
  onSave,
  saveLabel,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  size?: ModalSize;
  /** @deprecated use size="lg" */
  wide?: boolean;
  nested?: boolean;
  /** Optional save button — shown in header alongside window controls */
  onSave?: () => void;
  saveLabel?: string;
}) {
  const resolvedSize: ModalSize = size ?? (wide ? "lg" : "md");
  const zClass = nested ? "z-[60]" : "z-50";

  const [minimized, setMinimized] = useState(false);
  const [maximized, setMaximized] = useState(false);

  // Reset window state when modal opens/closes
  useEffect(() => {
    if (!open) {
      setMinimized(false);
      setMaximized(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (minimized) setMinimized(false);
        else onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, minimized]);

  useEffect(() => {
    if (!open || nested || minimized) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open, nested, minimized]);

  if (!open) return null;

  // ── Minimized state: floating title bar at bottom-right ──
  if (minimized) {
    return createPortal(
      <div
        className={`fixed bottom-4 right-4 ${zClass} glass rounded-lg border border-edge shadow-xl px-4 py-2 flex items-center gap-3 cursor-pointer hover:border-accent/50 transition-colors`}
        onClick={() => setMinimized(false)}
        title="点击恢复窗口"
      >
        <span className="text-xs text-accent2 truncate max-w-[240px]">{title}</span>
        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => setMinimized(false)}
            className="text-slate-500 hover:text-accent text-sm leading-none w-5 h-5 flex items-center justify-center rounded hover:bg-accent/10"
            title="恢复"
          >
            □
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onClose(); }}
            className="text-slate-500 hover:text-rose-400 text-sm leading-none w-5 h-5 flex items-center justify-center rounded hover:bg-rose-500/10"
            title="关闭"
          >
            ×
          </button>
        </div>
      </div>,
      document.body
    );
  }

  // ── Normal or maximized state ──
  const maxClass = maximized
    ? "max-w-[98vw] max-h-[98vh] w-[98vw] h-[98vh]"
    : `${SIZE_CLASS[resolvedSize]} max-h-[90vh]`;

  return createPortal(
    <div
      className={`fixed inset-0 ${zClass} flex items-center justify-center bg-black/60 backdrop-blur-sm p-4`}
      onClick={maximized ? undefined : onClose}
    >
      <div
        className={`glass rounded-xl border border-edge shadow-2xl w-full ${maxClass} flex flex-col transition-all duration-200`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Title bar ── */}
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-edge shrink-0">
          <h2 className="text-sm uppercase tracking-widest text-accent2 flex-1 min-w-0 truncate">
            {title}
          </h2>

          {/* Save button (optional) */}
          {onSave && (
            <button
              onClick={onSave}
              className="text-[11px] border border-accent text-accent rounded px-2.5 py-1 hover:bg-accent/10 transition-colors shrink-0"
              title="保存"
            >
              {saveLabel || "💾 保存"}
            </button>
          )}

          {/* Window controls */}
          <div className="flex items-center gap-0.5 shrink-0">
            <button
              onClick={() => setMinimized(true)}
              className="text-slate-500 hover:text-slate-300 text-sm leading-none w-6 h-6 flex items-center justify-center rounded hover:bg-slate-500/10"
              title="最小化"
            >
              _
            </button>
            <button
              onClick={() => setMaximized((m) => !m)}
              className="text-slate-500 hover:text-slate-300 text-sm leading-none w-6 h-6 flex items-center justify-center rounded hover:bg-slate-500/10"
              title={maximized ? "还原" : "最大化"}
            >
              {maximized ? "❐" : "□"}
            </button>
            <button
              onClick={onClose}
              className="text-slate-500 hover:text-rose-400 text-lg leading-none w-6 h-6 flex items-center justify-center rounded hover:bg-rose-500/10"
              title="关闭 (Esc)"
            >
              ×
            </button>
          </div>
        </div>

        {/* ── Body ── */}
        <div className="p-5 overflow-y-auto flex-1 min-h-0">{children}</div>
      </div>
    </div>,
    document.body
  );
}
