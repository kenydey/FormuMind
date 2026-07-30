/**
 * Run-status notifications, derived rather than stored.
 *
 * Every notification is computed from state the app already keeps — there is
 * no parallel list of notification objects. That is deliberate:
 *
 *  - The driving fields cannot go away. `kbIngest.docs` feeds the per-row
 *    badges in SourcesPanel, `searchProgress.total` feeds the search button
 *    label, `filterReport` is a typed API payload. A push model would
 *    *duplicate* them, not replace them.
 *  - The duplicate would go stale. `searchSlice.searchSources` reassigns
 *    `searchProgress` on every SSE frame; a mirrored notification would need a
 *    second write at every one of those sites.
 *  - Deriving makes "at most one notification per source" true by
 *    construction — one branch per kind, so there is no dedupe logic and no
 *    way to stack duplicates.
 *
 * Dismissal is therefore a *flag* (`notificationsDismissed`), never a deletion
 * of the driving field. Nulling `searchProgress` would not work: the SSE writer
 * reassigns it milliseconds later and the card would blink back. Nulling
 * `kbIngest` would additionally erase the per-row KB badges.
 *
 * Each flag is cleared when its operation next starts (see the slices), so
 * dismissing one run never hides the next one.
 */
import type { ComprehensiveReport, Evidence, FilterReport, SearchStreamProgress } from "../api";
import type { KbIngestState } from "./types";

export type NotificationTone = "info" | "success" | "warn" | "error" | "neutral" | "violet";

export type NotificationKind =
  | "error"
  | "deep-research"
  | "search"
  | "kb-ingest"
  | "kb-refresh"
  | "filter-report"
  | "seed-fallback"
  | "deep-report";

/**
 * Fixed render order. Errors first (the only thing needing immediate action),
 * then in-flight work, then post-run notices. A fixed order means the stack
 * never reshuffles under the cursor mid-click.
 */
export const NOTIFICATION_ORDER: NotificationKind[] = [
  "error",
  "deep-research",
  "search",
  "kb-ingest",
  "kb-refresh",
  "filter-report",
  "seed-fallback",
  "deep-report",
];

export const ALL_NOTIFICATION_KINDS: NotificationKind[] = NOTIFICATION_ORDER;

export interface DerivedNotification {
  kind: NotificationKind;
  tone: NotificationTone;
  title: string;
  message: string;
  /** 0..1, or null for "no progress bar". */
  progress: number | null;
  running: boolean;
  /** false ⇒ exempt from the collapse rule, and not counted toward it. */
  collapsible: boolean;
}

/** Search asks the backend for at most this many results (searchSlice: total_limit). */
export const SEARCH_TOTAL_LIMIT = 300;

/**
 * Un-dismiss these kinds, called at the start of the operation that drives them
 * so closing one run never suppresses the next. Mutates an immer draft's map
 * in place, which is why it is a helper rather than a store action — the slices
 * do it inside their own existing `set`, atomically with the rest of the reset.
 */
export function undismiss(
  flags: Record<NotificationKind, boolean>,
  kinds: readonly NotificationKind[]
): void {
  for (const k of kinds) flags[k] = false;
}

export function noNotificationsDismissed(): Record<NotificationKind, boolean> {
  return {
    error: false,
    "deep-research": false,
    search: false,
    "kb-ingest": false,
    "kb-refresh": false,
    "filter-report": false,
    "seed-fallback": false,
    "deep-report": false,
  };
}

function fraction(value: number, total: number, floor: number): number {
  if (total <= 0) return floor;
  return Math.min(1, Math.max(floor, value / total));
}

/**
 * Exactly the state the derivation reads. `AppState` satisfies this
 * structurally, so callers just pass the store — but naming the subset lets
 * NotificationStack select these fields with `useShallow` and derive during
 * render. Deriving inside the selector instead would hand zustand a fresh array
 * on every call and trip React's "getSnapshot should be cached" loop.
 */
export interface NotificationInputs {
  error: string | null;
  deepResearchBusy: boolean;
  deepResearchMessage: string;
  searchBusy: boolean;
  searchProgress: SearchStreamProgress | null;
  kbIngest: KbIngestState | null;
  kbRefreshNotice: string | null;
  filterReport: FilterReport | null;
  usedSeedFallback: boolean;
  sources: Evidence[];
  deepReport: ComprehensiveReport | null;
  notificationsDismissed: Record<NotificationKind, boolean>;
}

export function deriveNotifications(s: NotificationInputs): DerivedNotification[] {
  const dismissed = s.notificationsDismissed;
  const out: DerivedNotification[] = [];
  const push = (n: DerivedNotification) => {
    if (!dismissed[n.kind]) out.push(n);
  };

  if (s.error) {
    push({
      kind: "error",
      tone: "error",
      title: "⚠ 出错",
      message: s.error.replace(/^Error:\s*/, ""),
      progress: null,
      running: false,
      // Never collapsed: an error is the only thing the user must act on now.
      collapsible: false,
    });
  }

  if (s.deepResearchBusy) {
    push({
      kind: "deep-research",
      tone: "info",
      title: "深度研究 · CRAG",
      message: s.deepResearchMessage || "处理中…",
      // The six-stage strip carries the progress; the stack renders it as detail.
      progress: null,
      running: true,
      // The stage strip is the highest-value in-flight signal — keep it visible.
      collapsible: false,
    });
  }

  if (s.searchBusy && s.searchProgress) {
    push({
      kind: "search",
      tone: "info",
      title: "实时检索",
      message: s.searchProgress.message,
      progress: fraction(s.searchProgress.total, SEARCH_TOTAL_LIMIT, 0.08),
      running: true,
      collapsible: true,
    });
  }

  if (s.kbIngest) {
    push({
      kind: "kb-ingest",
      tone: "success",
      title: "📚 知识库构建",
      message: s.kbIngest.message,
      progress:
        s.kbIngest.total > 0
          ? fraction(s.kbIngest.done, s.kbIngest.total, 0.06)
          : null,
      running: s.kbIngest.active,
      collapsible: true,
    });
  }

  if (s.kbRefreshNotice) {
    push({
      kind: "kb-refresh",
      tone: "success",
      title: "📥 补充知识库",
      message: s.kbRefreshNotice,
      progress: null,
      running: false,
      collapsible: true,
    });
  }

  const settled = s.sources.length > 0 && !s.searchBusy;

  if (settled && s.filterReport && s.filterReport.dropped > 0) {
    const r = s.filterReport;
    push({
      kind: "filter-report",
      tone: "neutral",
      title: "质量过滤",
      message: `保留 ${r.kept} 条，剔除 ${r.dropped} 条低质量结果`,
      progress: null,
      running: false,
      collapsible: true,
    });
  }

  if (settled && s.usedSeedFallback) {
    push({
      kind: "seed-fallback",
      tone: "warn",
      title: "离线示例",
      message: "当前结果含离线示例摘要（非实时专利数据）",
      progress: null,
      running: false,
      collapsible: true,
    });
  }

  if (s.deepReport && !s.deepResearchBusy) {
    push({
      kind: "deep-report",
      tone: "violet",
      title: "深度研究报告",
      message: `已生成（${s.deepReport.citations?.length ?? 0} 条引用）— 见下方对话与左栏资料列表`,
      progress: null,
      running: false,
      collapsible: true,
    });
  }

  const rank = new Map(NOTIFICATION_ORDER.map((k, i) => [k, i]));
  return out.sort((a, b) => (rank.get(a.kind) ?? 0) - (rank.get(b.kind) ?? 0));
}
