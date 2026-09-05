/**
 * The centre-column notification stack.
 *
 * The load-bearing test here is the "wedge": closing a *running* notification
 * must hide the card and leave the operation running. The requirement was a
 * close button on every notification, and the temptation is to implement that
 * as cancellation — but aborting the client stream would not stop the
 * server-side task, so a `×` that claimed to cancel would silently lose the
 * user a search. `searchBusy` staying true after the click is what pins the
 * honest behaviour.
 *
 * The store is driven with `setState` rather than a mocked api module: the
 * stack performs no I/O, and importing the store does not fire any request
 * (hydrateLlmSettings/initProjects are called from App, not at import).
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "../store";
import { noNotificationsDismissed, undismiss } from "../store/notifications";
import type { AppState } from "../store/types";
import NotificationStack from "./NotificationStack";

const IDLE = {
  error: null,
  deepResearchBusy: false,
  deepResearchStage: "",
  deepResearchMessage: "",
  searchBusy: false,
  searchProgress: null,
  kbIngest: null,
  filterReport: null,
  usedSeedFallback: false,
  sources: [],
  deepReport: null,
  notificationsDismissed: noNotificationsDismissed(),
} as unknown as Partial<AppState>;

const progress = (over: Record<string, unknown> = {}) => ({
  message: "检索中…",
  total: 60,
  source: "patents",
  newCount: 2,
  sourcesDone: ["arxiv"],
  sourcesPending: ["internet"],
  ...over,
});

const kbIngest = (over: Record<string, unknown> = {}) => ({
  taskId: "t1",
  docs: [],
  done: 2,
  total: 4,
  indexed: 2,
  failed: 0,
  message: "入库中",
  active: true,
  ...over,
});

function setState(patch: Partial<AppState>) {
  useStore.setState({ ...IDLE, ...patch } as AppState);
}

/** The close button on the card whose title contains `title`. */
function closeButtonFor(title: string): HTMLElement {
  const card = screen.getByText(title).closest("div.rounded-lg") as HTMLElement;
  expect(card).toBeTruthy();
  const close = Array.from(card.querySelectorAll("button")).find(
    (b) => b.getAttribute("aria-label") === "关闭"
  );
  expect(close).toBeTruthy();
  return close as HTMLElement;
}

describe("NotificationStack", () => {
  beforeEach(() => {
    setState({});
  });

  it("renders nothing when the app is idle", () => {
    const { container } = render(<NotificationStack />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the error and clears it on close", async () => {
    setState({ error: "502 Bad Gateway" });
    render(<NotificationStack />);
    expect(screen.getByText("502 Bad Gateway")).toBeTruthy();

    await userEvent.click(closeButtonFor("⚠ 出错"));
    expect(useStore.getState().error).toBeNull();
  });

  it("carries the backend-down hint that only the left column used to show", () => {
    setState({ error: "Failed to fetch" });
    render(<NotificationStack />);
    expect(screen.getByText(/uvicorn app.main:app/)).toBeTruthy();
  });

  it("shows the backend-down hint for WebKit Load failed and proxy 5xx", () => {
    setState({ error: "Load failed" });
    const { unmount } = render(<NotificationStack />);
    expect(screen.getByText(/uvicorn app.main:app/)).toBeTruthy();
    unmount();

    setState({ error: "/api/projects -> 500" });
    render(<NotificationStack />);
    expect(screen.getByText(/uvicorn app.main:app/)).toBeTruthy();
  });

  it("hides a running search on close without stopping the search", async () => {
    setState({ searchBusy: true, searchProgress: progress() as never });
    render(<NotificationStack />);
    expect(screen.getByText("实时检索")).toBeTruthy();

    await userEvent.click(closeButtonFor("实时检索"));

    expect(screen.queryByText("实时检索")).toBeNull();
    // The wedge: dismissal is display-only.
    expect(useStore.getState().searchBusy).toBe(true);
    expect(useStore.getState().searchProgress).not.toBeNull();
  });

  it("shows the search card again on the next search", async () => {
    setState({ searchBusy: true, searchProgress: progress() as never });
    render(<NotificationStack />);
    await userEvent.click(closeButtonFor("实时检索"));
    expect(screen.queryByText("实时检索")).toBeNull();

    // What searchSources does at the top of a new run.
    // The store is immer-wrapped, so mutate the draft — same call the slices make.
    act(() => useStore.setState((s) => undismiss(s.notificationsDismissed, ["search"])));
    expect(screen.getByText("实时检索")).toBeTruthy();
  });

  it("keeps a dismissed search hidden across further progress frames", async () => {
    // A second, undismissed card rides along so the new-frame assertion cannot
    // pass merely because nothing re-rendered.
    setState({
      searchBusy: true,
      searchProgress: progress() as never,
      kbIngest: kbIngest() as never,
    });
    render(<NotificationStack />);
    await userEvent.click(closeButtonFor("实时检索"));

    act(() =>
      useStore.setState({
        searchProgress: progress({ total: 210, message: "又一帧" }),
        kbIngest: kbIngest({ message: "第二帧" }),
      } as never)
    );

    expect(screen.getByText("第二帧")).toBeTruthy(); // proof a re-render happened
    expect(screen.queryByText("实时检索")).toBeNull();
    expect(screen.queryByText("又一帧")).toBeNull();
  });

  it("hides the KB card on close but keeps kbIngest for the per-row badges", async () => {
    setState({ kbIngest: kbIngest() as never });
    render(<NotificationStack />);
    await userEvent.click(closeButtonFor("📚 知识库构建"));

    expect(screen.queryByText("📚 知识库构建")).toBeNull();
    // SourcesPanel builds its per-row badges from kbIngest.docs — nulling it
    // here (as the old dismissKbIngest did) would erase them too.
    expect(useStore.getState().kbIngest).not.toBeNull();
  });

  it("renders the deep-research stage strip supplied by the panel", () => {
    setState({ deepResearchBusy: true, deepResearchMessage: "正在评估" });
    render(<NotificationStack renderDetail={() => <span>阶段条</span>} />);
    expect(screen.getByText("深度研究 · CRAG")).toBeTruthy();
    expect(screen.getByText("阶段条")).toBeTruthy();
    expect(closeButtonFor("深度研究 · CRAG")).toBeTruthy();
  });

  it("collapses more than two collapsible notices into one row", async () => {
    setState({
      searchBusy: true,
      searchProgress: progress() as never,
      kbIngest: kbIngest() as never,
      deepReport: { citations: [] } as never,
    });
    render(<NotificationStack />);

    expect(screen.getByText(/⋯ 3 条状态/)).toBeTruthy();
    expect(screen.queryByText("实时检索")).toBeNull();

    await userEvent.click(screen.getByText("▾ 展开全部"));
    expect(screen.getByText("实时检索")).toBeTruthy();
    expect(screen.getByText("📚 知识库构建")).toBeTruthy();
  });

  it("does not collapse two notices", () => {
    setState({
      kbIngest: kbIngest() as never,
      sources: [{ title: "a", identifier: "a" }] as never,
      filterReport: { kept: 10, dropped: 3, dropped_by_reason: {}, dropped_examples: [] } as never,
    });
    render(<NotificationStack />);
    expect(screen.queryByText(/条状态/)).toBeNull();
    expect(screen.getByText("📚 知识库构建")).toBeTruthy();
    expect(screen.getByText("质量过滤")).toBeTruthy();
  });

  it("keeps the error visible alongside a collapsed row", () => {
    setState({
      error: "boom",
      searchBusy: true,
      searchProgress: progress() as never,
      kbIngest: kbIngest() as never,
      deepReport: { citations: [] } as never,
    });
    render(<NotificationStack />);
    expect(screen.getByText("⚠ 出错")).toBeTruthy();
    expect(screen.getByText("boom")).toBeTruthy();
    expect(screen.getByText(/⋯ 3 条状态/)).toBeTruthy();
  });

  it("closes every collapsed notice in one click", async () => {
    setState({
      searchBusy: true,
      searchProgress: progress() as never,
      kbIngest: kbIngest() as never,
      deepReport: { citations: [] } as never,
    });
    const { container } = render(<NotificationStack />);
    await userEvent.click(closeButtonFor("运行状态"));
    expect(container.firstChild).toBeNull();
  });

  it("reports how many of the collapsed notices are still running", () => {
    setState({
      searchBusy: true,
      searchProgress: progress() as never,
      kbIngest: kbIngest({ active: false, message: "完成" }) as never,
      deepReport: { citations: [] } as never,
    });
    render(<NotificationStack />);
    expect(screen.getByText(/1 项进行中/)).toBeTruthy();
  });

  it("shows the filter breakdown behind a toggle", async () => {
    setState({
      sources: [{ title: "a", identifier: "a" }] as never,
      filterReport: {
        kept: 10,
        dropped: 3,
        dropped_by_reason: { blocked_domain: 3 },
        dropped_examples: ["spam.example.com"],
      } as never,
    });
    render(<NotificationStack />);
    expect(screen.getByText(/保留 10 条，剔除 3 条/)).toBeTruthy();
    expect(screen.queryByText(/屏蔽域名/)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "展开明细" }));
    expect(screen.getByText(/屏蔽域名: 3/)).toBeTruthy();
    expect(screen.getByText("spam.example.com")).toBeTruthy();
  });
});
