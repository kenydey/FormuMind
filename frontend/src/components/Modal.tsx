import { useEffect } from "react";

export default function Modal({
  title,
  open,
  onClose,
  children,
  wide,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className={`glass rounded-xl border border-edge shadow-2xl w-full ${
          wide ? "max-w-4xl" : "max-w-2xl"
        } max-h-[90vh] flex flex-col`}
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
        <div className="p-5 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}
