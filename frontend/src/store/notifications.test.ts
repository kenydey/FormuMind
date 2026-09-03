/**
 * Run-status notification derivation.
 *
 * Two invariants matter more than the individual branches:
 *
 *  - **One entry per source.** The whole reason notifications are derived
 *    rather than pushed is that duplicates then become impossible by
 *    construction. If that ever stops holding, the design has been lost.
 *  - **Dismissal survives the writer.** `searchSources` reassigns
 *    `searchProgress` on every SSE frame. If dismissal were implemented by
 *    nulling the source field instead of setting a flag, the card would blink
 *    back within milliseconds. One test below reproduces exactly that frame.
 */
import { describe, expect, it } from "vitest";
import {
  ALL_NOTIFICATION_KINDS,
  deriveNotifications,
  noNotificationsDismissed,
  NOTIFICATION_ORDER,
  SEARCH_TOTAL_LIMIT,
} from "./notifications";
import type { NotificationInputs, NotificationKind } from "./notifications";

/** Every notification driver switched on at once. */
function allDrivers(over: Partial<NotificationInputs> = {}): NotificationInputs {
  return {
    error: "boom",
    deepResearchBusy: true,
    deepResearchMessage: "正在评估",
    searchBusy: false,
    searchProgress: {
      message: "检索中…",
      total: 150,
      source: "patents",
      newCount: 3,
      sourcesDone: ["patents"],
      sourcesPending: ["arxiv"],
    },
    kbIngest: {
      taskId: "t1",
      docs: [],
      done: 4,
      total: 8,
      indexed: 4,
      failed: 0,
      message: "入库中",
      active: true,
    },
    filterReport: { kept: 10, dropped: 4, dropped_by_reason: {}, dropped_examples: [] },
    usedSeedFallback: true,
    sources: [{ title: "a", identifier: "a" }],
    deepReport: { citations: [{ title: "c", identifier: "c" }] },
    notificationsDismissed: noNotificationsDismissed(),
    ...over,
  } as NotificationInputs;
}

const kindsOf = (s: NotificationInputs): NotificationKind[] =>
  deriveNotifications(s).map((n) => n.kind);

describe("deriveNotifications", () => {
  it("emits at most one entry per kind with every driver set", () => {
    // `deepResearchBusy` suppresses deep-report, and `searchBusy` gates the
    // settled notices, so drive them in the two states that between them
    // reach every branch.
    for (const state of [
      allDrivers({ searchBusy: true, deepResearchBusy: true }),
      allDrivers({ searchBusy: false, deepResearchBusy: false }),
    ]) {
      const kinds = kindsOf(state);
      expect(new Set(kinds).size).toBe(kinds.length);
    }
  });

  it("covers every declared kind across the two gate states", () => {
    const seen = new Set([
      ...kindsOf(allDrivers({ searchBusy: true, deepResearchBusy: true })),
      ...kindsOf(allDrivers({ searchBusy: false, deepResearchBusy: false })),
    ]);
    expect([...seen].sort()).toEqual([...ALL_NOTIFICATION_KINDS].sort());
  });

  it("orders deterministically with errors first", () => {
    const kinds = kindsOf(allDrivers({ searchBusy: false, deepResearchBusy: false }));
    expect(kinds[0]).toBe("error");
    const rank = (k: NotificationKind) => NOTIFICATION_ORDER.indexOf(k);
    for (let i = 1; i < kinds.length; i++) {
      expect(rank(kinds[i])).toBeGreaterThan(rank(kinds[i - 1]));
    }
  });

  it("suppresses exactly the dismissed kind and nothing else", () => {
    for (const kind of ALL_NOTIFICATION_KINDS) {
      // Pick the gate state that actually emits this kind.
      const base = ["deep-research", "search"].includes(kind)
        ? { searchBusy: true, deepResearchBusy: true }
        : { searchBusy: false, deepResearchBusy: false };
      const before = kindsOf(allDrivers(base));
      expect(before).toContain(kind);

      const dismissed = noNotificationsDismissed();
      dismissed[kind] = true;
      const after = kindsOf(allDrivers({ ...base, notificationsDismissed: dismissed }));

      expect(after).not.toContain(kind);
      expect(after).toEqual(before.filter((k) => k !== kind));
    }
  });

  it("keeps a dismissed search hidden when the SSE writer reassigns searchProgress", () => {
    // This is the bug the flag design exists to prevent: searchSources writes
    // draft.searchProgress on every frame, so a dismissal that nulled the field
    // would be undone by the very next event.
    const dismissed = noNotificationsDismissed();
    dismissed.search = true;
    const state = allDrivers({ searchBusy: true, notificationsDismissed: dismissed });
    expect(kindsOf(state)).not.toContain("search");

    // Simulate the next SSE frame: a brand-new progress object.
    const next = allDrivers({
      searchBusy: true,
      notificationsDismissed: dismissed,
      searchProgress: {
        message: "检索中…（新帧）",
        total: 220,
        source: "arxiv",
        newCount: 9,
        sourcesDone: ["patents", "arxiv"],
        sourcesPending: [],
      },
    });
    expect(kindsOf(next)).not.toContain("search");
  });

  it("hides the settled notices while a search is running", () => {
    const running = kindsOf(allDrivers({ searchBusy: true }));
    expect(running).not.toContain("filter-report");
    expect(running).not.toContain("seed-fallback");
  });

  it("requires loaded sources for the settled notices", () => {
    const empty = kindsOf(allDrivers({ searchBusy: false, sources: [] }));
    expect(empty).not.toContain("filter-report");
    expect(empty).not.toContain("seed-fallback");
  });

  it("hides the filter report when nothing was dropped", () => {
    const state = allDrivers({
      searchBusy: false,
      filterReport: { kept: 10, dropped: 0, dropped_by_reason: {}, dropped_examples: [] },
    });
    expect(kindsOf(state)).not.toContain("filter-report");
  });

  it("hides the deep-research report while deep research is still running", () => {
    expect(kindsOf(allDrivers({ deepResearchBusy: true }))).not.toContain("deep-report");
    expect(kindsOf(allDrivers({ deepResearchBusy: false }))).toContain("deep-report");
  });

  it("exempts errors and running deep research from collapsing", () => {
    const all = deriveNotifications(allDrivers({ searchBusy: true, deepResearchBusy: true }));
    const pinned = all.filter((n) => !n.collapsible).map((n) => n.kind);
    expect(pinned.sort()).toEqual(["deep-research", "error"]);
  });

  it("derives search progress as a fraction of the backend's own limit", () => {
    const n = deriveNotifications(allDrivers({ searchBusy: true })).find(
      (x) => x.kind === "search"
    )!;
    expect(n.progress).toBeCloseTo(150 / SEARCH_TOTAL_LIMIT);
  });

  it("clamps search progress into a visible range", () => {
    const at0 = deriveNotifications(
      allDrivers({
        searchBusy: true,
        searchProgress: {
          message: "正在排队…",
          total: 0,
          source: null,
          newCount: 0,
          sourcesDone: [],
          sourcesPending: [],
        },
      })
    ).find((x) => x.kind === "search")!;
    expect(at0.progress).toBeGreaterThan(0);

    const over = deriveNotifications(
      allDrivers({
        searchBusy: true,
        searchProgress: {
          message: "很多",
          total: 9999,
          source: null,
          newCount: 0,
          sourcesDone: [],
          sourcesPending: [],
        },
      })
    ).find((x) => x.kind === "search")!;
    expect(over.progress).toBe(1);
  });

  it("omits the KB progress bar until a total is known", () => {
    const n = deriveNotifications(
      allDrivers({
        kbIngest: {
          taskId: "t",
          docs: [],
          done: 0,
          total: 0,
          indexed: 0,
          failed: 0,
          message: "排队中",
          active: true,
        },
      })
    ).find((x) => x.kind === "kb-ingest")!;
    expect(n.progress).toBeNull();
  });

  it("marks a finished KB build as no longer running but still shows it", () => {
    const n = deriveNotifications(
      allDrivers({
        kbIngest: {
          taskId: "t",
          docs: [],
          done: 8,
          total: 8,
          indexed: 8,
          failed: 0,
          message: "知识库构建完成",
          active: false,
        },
      })
    ).find((x) => x.kind === "kb-ingest")!;
    expect(n.running).toBe(false);
    expect(n.message).toBe("知识库构建完成");
  });

  it("emits nothing when the app is idle", () => {
    const idle: NotificationInputs = {
      error: null,
      deepResearchBusy: false,
      deepResearchMessage: "",
      searchBusy: false,
      searchProgress: null,
      kbIngest: null,
      filterReport: null,
      usedSeedFallback: false,
      sources: [],
      deepReport: null,
      notificationsDismissed: noNotificationsDismissed(),
    };
    expect(deriveNotifications(idle)).toEqual([]);
  });

  it("strips the redundant Error: prefix from the message", () => {
    const n = deriveNotifications(allDrivers({ error: "Error: 502 Bad Gateway" }))[0];
    expect(n.kind).toBe("error");
    expect(n.message).toBe("502 Bad Gateway");
  });
});
