import { useEffect } from "react";
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
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  size?: ModalSize;
  /** @deprecated use size="lg" */
  wide?: boolean;
  nested?: boolean;
}) {
  const resolvedSize: ModalSize = size ?? (wide ? "lg" : "md");
  const zClass = nested ? "z-[60]" : "z-50";

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open || nested) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open, nested]);

  if (!open) return null;

  return createPortal(
    <div
      className={`fixed inset-0 ${zClass} flex items-center justify-center bg-black/60 backdrop-blur-sm p-4`}
      onClick={onClose}
    >
      <div
        className={`glass rounded-xl border border-edge shadow-2xl w-full ${SIZE_CLASS[resolvedSize]} max-h-[90vh] flex flex-col`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-edge shrink-0">
          <h2 className="text-sm uppercase tracking-widest text-accent2">{title}</h2>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-rose-400 text-lg leading-none w-6 h-6 flex items-center justify-center rounded hover:bg-rose-500/10"
            title="关闭 (Esc)"
          >
            ×
          </button>
        </div>
        <div className="p-5 overflow-y-auto flex-1 min-h-0">{children}</div>
      </div>
    </div>,
    document.body
  );
}
