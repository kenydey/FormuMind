/**
 * One run-status notification.
 *
 * Presentational only — it takes no store, so it can be tested without one.
 * The tone classes are copied verbatim from the cards this replaces
 * (SourcesPanel's search/KB cards, ResearchPanel's deep-research and report
 * banners) so consolidating them causes no visual drift.
 *
 * `onDismiss` is required rather than optional: "every notification has a
 * close button" is the requirement, and making the prop mandatory means the
 * type checker enforces it instead of a reviewer having to notice.
 *
 * Outer spacing belongs to NotificationStack, not here, so stacked cards get
 * clean gaps instead of colliding margins.
 */
import { useState } from "react";
import type { ReactNode } from "react";
import type { NotificationTone } from "../store/notifications";

const TONE_BOX: Record<NotificationTone, string> = {
  info: "border-accent/30 bg-accent/5",
  success: "border-teal-500/25 bg-teal-500/5",
  warn: "border-amber-500/30 bg-amber-500/10",
  error: "border-rose-500/40 bg-rose-500/10",
  neutral: "border-slate-600/40 bg-slate-800/40",
  violet: "border-violet-500/30 bg-violet-500/5",
};

const TONE_TITLE: Record<NotificationTone, string> = {
  info: "text-accent2",
  success: "text-teal-300",
  warn: "text-amber-200/90",
  error: "text-rose-300",
  neutral: "text-slate-300",
  violet: "text-violet-200",
};

const TONE_BAR: Record<NotificationTone, string> = {
  info: "bg-accent/80",
  success: "bg-teal-400/80",
  warn: "bg-amber-400/80",
  error: "bg-rose-400/80",
  neutral: "bg-slate-400/80",
  violet: "bg-violet-400/80",
};

const TONE_MESSAGE: Record<NotificationTone, string> = {
  info: "text-slate-300",
  success: "text-slate-400",
  warn: "text-amber-200/90",
  error: "text-rose-300",
  neutral: "text-slate-300",
  violet: "text-violet-200",
};

export interface NotificationCardProps {
  tone: NotificationTone;
  title: string;
  message?: string;
  /** 0..1. Omit or pass null for no progress bar. */
  progress?: number | null;
  pulse?: boolean;
  /** Chips, stage strip, hints — anything richer than a one-line message. */
  detail?: ReactNode;
  /** Put `detail` behind a ▾/▴ toggle instead of always showing it. */
  disclosure?: boolean;
  onDismiss: () => void;
}

export default function NotificationCard({
  tone,
  title,
  message,
  progress,
  pulse = false,
  detail,
  disclosure = false,
  onDismiss,
}: NotificationCardProps) {
  const [open, setOpen] = useState(false);
  const showDetail = detail != null && (!disclosure || open);

  return (
    <div className={`rounded-lg border px-3 py-2 text-[11px] ${TONE_BOX[tone]}`}>
      <div className="flex items-center justify-between gap-2">
        <span className={`uppercase tracking-widest shrink-0 ${TONE_TITLE[tone]}`}>
          {title}
        </span>
        {message && (
          // flex-1 + text-right keeps the status hugging the close button, the
          // way the label/status split read before the `×` became a third child.
          <span
            className={`flex-1 min-w-0 text-right truncate ${TONE_MESSAGE[tone]} ${
              pulse ? "animate-pulse" : ""
            }`}
            title={message}
          >
            {message}
          </span>
        )}
        {disclosure && detail != null && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="shrink-0 text-slate-500 hover:text-slate-300 leading-none w-5 h-5 flex items-center justify-center rounded"
            title={open ? "收起明细" : "展开明细"}
            aria-label={open ? "收起明细" : "展开明细"}
          >
            {open ? "▴" : "▾"}
          </button>
        )}
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 text-slate-500 hover:text-rose-400 leading-none w-5 h-5 flex items-center justify-center rounded hover:bg-rose-500/10"
          title="关闭"
          aria-label="关闭"
        >
          ×
        </button>
      </div>

      {progress != null && (
        <div className="mt-1.5 h-1 bg-edge rounded overflow-hidden">
          <div
            className={`h-full transition-all duration-300 ${TONE_BAR[tone]} ${
              pulse ? "animate-pulse" : ""
            }`}
            style={{ width: `${Math.min(100, Math.max(0, progress * 100))}%` }}
          />
        </div>
      )}

      {showDetail && <div className="mt-1.5">{detail}</div>}
    </div>
  );
}
