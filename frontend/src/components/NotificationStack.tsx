/**
 * All run-status notifications, in one place at the top of the centre column.
 *
 * They used to be spread down the left column, where six `shrink-0` blocks sat
 * above the loaded-sources list and squeezed it. Only deep research reported
 * from the centre. Consolidating here gives the sources list its space back and
 * gives every notice the same close button.
 *
 * The list is derived from store state (see store/notifications.ts) — this
 * component owns presentation and the collapse rule, nothing else.
 */
import { useState } from "react";
import type { ReactNode } from "react";
import { useShallow } from "zustand/react/shallow";
import { sourceLabel } from "../constants/sourceLabels";
import { useStore } from "../store";
import { deriveNotifications } from "../store/notifications";
import type {
  DerivedNotification,
  NotificationInputs,
  NotificationKind,
  NotificationTone,
} from "../store/notifications";
import NotificationCard from "./NotificationCard";

/** Above this many collapsible notices the stack folds into one line. */
const COLLAPSE_ABOVE = 2;

const FILTER_REASON_LABELS: Record<string, string> = {
  blocked_domain: "屏蔽域名",
  garbage_snippet: "无效摘要",
  near_duplicate: "近似重复",
  llm_judge: "LLM 质检",
};

const TONE_SEVERITY: Record<NotificationTone, number> = {
  error: 5,
  warn: 4,
  info: 3,
  violet: 2,
  success: 1,
  neutral: 0,
};

type DetailMap = Partial<Record<NotificationKind, { detail: ReactNode; disclosure?: boolean }>>;

/** Extra content per notification kind — chips, hints, breakdowns. */
function buildDetails(s: NotificationInputs): DetailMap {
  const { error, searchProgress, filterReport } = s;
  const details: DetailMap = {};

  // The left-column error banner carried this hint and the centre one did not —
  // it is the most actionable line in the whole app when the backend is down.
  if (error && error.includes("fetch")) {
    details.error = {
      detail: (
        <span className="text-rose-400/70">
          请确认后端已启动：
          <code className="text-rose-300 bg-ink/60 rounded px-1">
            uvicorn app.main:app --port 8000
          </code>
        </span>
      ),
    };
  }

  if (searchProgress) {
    const pending = searchProgress.sourcesPending
      .filter((s) => s !== searchProgress.source)
      .slice(0, 6);
    details.search = {
      detail: (
        <div className="flex flex-wrap gap-1.5">
          {searchProgress.sourcesDone.map((s) => (
            <span
              key={`done-${s}`}
              className="px-1.5 py-0.5 rounded bg-teal-500/15 text-teal-300 border border-teal-500/20"
            >
              ✓ {sourceLabel(s)}
            </span>
          ))}
          {searchProgress.source && !searchProgress.sourcesDone.includes(searchProgress.source) && (
            <span className="px-1.5 py-0.5 rounded bg-accent/15 text-accent border border-accent/30 animate-pulse">
              … {sourceLabel(searchProgress.source)}
            </span>
          )}
          {pending.map((s) => (
            <span
              key={`pending-${s}`}
              className="px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-500 border border-edge/60"
            >
              {sourceLabel(s)}
            </span>
          ))}
        </div>
      ),
    };
  }

  if (filterReport && filterReport.dropped > 0) {
    details["filter-report"] = {
      disclosure: true,
      detail: (
        <div className="space-y-1 text-[10px] text-slate-400 border-t border-edge/40 pt-1.5">
          <div className="flex flex-wrap gap-1">
            {Object.entries(filterReport.dropped_by_reason).map(([reason, count]) => (
              <span key={reason} className="px-1 py-0.5 rounded border border-edge/60 bg-ink/50">
                {FILTER_REASON_LABELS[reason] ?? reason}: {count}
              </span>
            ))}
          </div>
          {filterReport.dropped_examples.length > 0 && (
            <ul className="list-disc list-inside text-slate-500 max-h-20 overflow-y-auto">
              {filterReport.dropped_examples.map((ex, i) => (
                <li key={i} className="truncate" title={ex}>
                  {ex}
                </li>
              ))}
            </ul>
          )}
        </div>
      ),
    };
  }

  return details;
}

export interface NotificationStackProps {
  /**
   * Panel-owned detail for a notification kind. ResearchPanel uses this to keep
   * the six-stage CRAG strip — chat-panel domain knowledge — inside its own
   * file while still rendering it through this stack.
   */
  renderDetail?: (kind: NotificationKind) => ReactNode;
}

export default function NotificationStack({ renderDetail }: NotificationStackProps) {
  // Select the driver fields, then derive during render. Deriving inside the
  // selector would return a fresh array every call and trip React's
  // "getSnapshot should be cached" loop.
  const inputs = useStore(
    useShallow(
      (s): NotificationInputs => ({
        error: s.error,
        deepResearchBusy: s.deepResearchBusy,
        deepResearchMessage: s.deepResearchMessage,
        searchBusy: s.searchBusy,
        searchProgress: s.searchProgress,
        kbIngest: s.kbIngest,
        filterReport: s.filterReport,
        usedSeedFallback: s.usedSeedFallback,
        sources: s.sources,
        deepReport: s.deepReport,
        notificationsDismissed: s.notificationsDismissed,
      })
    )
  );
  const dismissNotification = useStore((s) => s.dismissNotification);
  const notifications = deriveNotifications(inputs);
  const details = buildDetails(inputs);
  const [expanded, setExpanded] = useState(false);

  if (notifications.length === 0) return null;

  const pinned = notifications.filter((n) => !n.collapsible);
  const rest = notifications.filter((n) => n.collapsible);
  const collapsed = rest.length > COLLAPSE_ABOVE && !expanded;

  const card = (n: DerivedNotification) => {
    const own = details[n.kind];
    const panelDetail = renderDetail?.(n.kind);
    return (
      <NotificationCard
        key={n.kind}
        tone={n.tone}
        title={n.title}
        message={n.message}
        progress={n.progress}
        pulse={n.running}
        detail={panelDetail ?? own?.detail}
        disclosure={panelDetail ? false : own?.disclosure}
        onDismiss={() => dismissNotification(n.kind)}
      />
    );
  };

  return (
    <div className="shrink-0 px-4 pt-3 pb-1 flex flex-col gap-1.5 max-h-[45%] overflow-y-auto">
      {pinned.map(card)}
      {collapsed ? (
        <CollapsedRow
          items={rest}
          onExpand={() => setExpanded(true)}
          onDismissAll={() => rest.forEach((n) => dismissNotification(n.kind))}
        />
      ) : (
        <>
          {rest.map(card)}
          {rest.length > COLLAPSE_ABOVE && (
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="self-start text-[10px] text-slate-500 hover:text-slate-300"
            >
              ▴ 收起状态
            </button>
          )}
        </>
      )}
    </div>
  );
}

function CollapsedRow({
  items,
  onExpand,
  onDismissAll,
}: {
  items: DerivedNotification[];
  onExpand: () => void;
  onDismissAll: () => void;
}) {
  const tone = items.reduce<NotificationTone>(
    (worst, n) => (TONE_SEVERITY[n.tone] > TONE_SEVERITY[worst] ? n.tone : worst),
    "neutral"
  );
  const running = items.filter((n) => n.running).length;
  const summary =
    `⋯ ${items.length} 条状态` +
    (running > 0 ? ` · ${running} 项进行中` : "") +
    ` —— ${items.map((n) => n.title).join("、")}`;

  return (
    <NotificationCard
      tone={tone}
      title="运行状态"
      message={summary}
      detail={
        <button
          type="button"
          onClick={onExpand}
          className="text-[10px] text-slate-400 hover:text-slate-200"
        >
          ▾ 展开全部
        </button>
      }
      onDismiss={onDismissAll}
    />
  );
}
