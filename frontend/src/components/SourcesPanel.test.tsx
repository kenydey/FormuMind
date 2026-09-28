/**
 * The left column, after the run-status notifications moved to the centre.
 *
 * These tests exist to pin *both* halves of that deletion: the ChemCrow
 * dependency badge is gone (it duplicated 设置 → 依赖 and cost the sources list
 * vertical space), while the per-row KB ingest badges and the per-source-type
 * status dots — the two things explicitly kept — are still there. Asserting
 * only the removal would let a future change quietly take the badges with it.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import { noNotificationsDismissed } from "../store/notifications";
import type { AppState } from "../store/types";
import SourcesPanel from "./SourcesPanel";

const CHEMCROW_STATUS = {
  chemcrow: { available: false, hint: "pip install chemcrow" },
  patents: { available: true },
};

function setState(patch: Partial<AppState>) {
  useStore.setState({
    searchQuery: "",
    // Both source types that used to gate the ChemCrow badge.
    sourceTypes: ["literature", "internet"],
    sources: [],
    selectedSources: [],
    sourceStatus: CHEMCROW_STATUS,
    searchBusy: false,
    searchProgress: null,
    kbIngest: null,
    deepResearchBusy: false,
    deepResearchMessage: "",
    error: null,
    usedSeedFallback: false,
    filterReport: null,
    notificationsDismissed: noNotificationsDismissed(),
    ...patch,
  } as AppState);
}

describe("SourcesPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "getSourceStatus").mockResolvedValue(CHEMCROW_STATUS as never);
    setState({});
  });

  it("no longer shows the ChemCrow dependency badge", async () => {
    render(<SourcesPanel />);
    await waitFor(() => expect(api.getSourceStatus).toHaveBeenCalled());
    // Visible in 设置 → 依赖; it does not need to occupy the left column.
    expect(screen.queryByText(/ChemCrow 化学增强检索/)).toBeNull();
    expect(screen.queryByText(/pip install chemcrow/)).toBeNull();
  });

  it("keeps the per-source-type status dots", () => {
    const { container } = render(<SourcesPanel />);
    // SourceTypePicker renders one availability dot per selected type.
    expect(container.querySelectorAll("span.rounded-full").length).toBeGreaterThan(0);
  });

  it("keeps the per-row KB ingest badge on each source", () => {
    setState({
      sources: [{ title: "专利 A", identifier: "cn-1", source: "patents" }] as never,
      selectedSources: ["cn-1"],
      kbIngest: {
        taskId: "t1",
        docs: [{ identifier: "cn-1", status: "indexed", error: null }],
        done: 1,
        total: 1,
        indexed: 1,
        failed: 0,
        message: "完成",
        active: false,
      } as never,
    });
    render(<SourcesPanel />);
    expect(screen.getByText("专利 A")).toBeTruthy();
    expect(screen.getByText("已入库")).toBeTruthy();
  });

  it("no longer renders the run-status cards that moved to the centre", () => {
    setState({
      searchBusy: true,
      searchProgress: {
        message: "检索中…",
        total: 60,
        source: "patents",
        newCount: 1,
        sourcesDone: ["arxiv"],
        sourcesPending: [],
      } as never,
      kbIngest: {
        taskId: "t1",
        docs: [],
        done: 1,
        total: 2,
        indexed: 1,
        failed: 0,
        message: "入库中",
        active: true,
      } as never,
      error: "boom",
      usedSeedFallback: true,
      sources: [{ title: "x", identifier: "x", source: "patents" }] as never,
      filterReport: {
        kept: 1,
        dropped: 2,
        dropped_by_reason: {},
        dropped_examples: [],
      } as never,
    });
    render(<SourcesPanel />);

    expect(screen.queryByText("实时检索")).toBeNull();
    expect(screen.queryByText(/知识库构建/)).toBeNull();
    expect(screen.queryByText(/boom/)).toBeNull();
    expect(screen.queryByText(/离线示例摘要/)).toBeNull();
    expect(screen.queryByText(/质量过滤/)).toBeNull();
  });

  it("still reports run state on the buttons themselves", () => {
    setState({
      searchQuery: "环氧",
      searchBusy: true,
      searchProgress: {
        message: "检索中…",
        total: 42,
        source: null,
        newCount: 0,
        sourcesDone: [],
        sourcesPending: [],
      } as never,
    });
    render(<SourcesPanel />);
    // The left column keeps its own run indication, which is what makes
    // dismissing the centre card safe.
    expect(screen.getByText("检索中（42 条）…")).toBeTruthy();
  });

  it("keeps the deep-research button label driven by its own message", () => {
    setState({ searchQuery: "环氧", deepResearchBusy: true, deepResearchMessage: "正在评估" });
    render(<SourcesPanel />);
    expect(screen.getByText("🔬 正在评估")).toBeTruthy();
  });

  it("W3-14: shows page badge and jumps to the chunk on click", async () => {
    vi.spyOn(api, "kbChunksBySource").mockResolvedValue([
      { chunk_id: "c1", text: "p5 text", page: 5 },
    ] as never);
    setState({
      sources: [
        { title: "文献 A", identifier: "lit-1", source: "literature", page: 5 },
      ] as never,
      selectedSources: ["lit-1"],
      kbIngest: {
        docs: [{ identifier: "lit-1", status: "indexed", source_id: "src-42", error: null }],
        done: 1,
        total: 1,
        indexed: 1,
        failed: 0,
        message: "完成",
        active: false,
      } as never,
    });
    render(<SourcesPanel />);
    const badge = await screen.findByTestId("page-jump-lit-1");
    expect(badge).toHaveTextContent("p.5");
    badge.click();
    // 资料详情弹窗打开并请求切块(带 focusPage=5 滚动到对应 chunk)
    await waitFor(() => {
      expect(screen.getByTestId("modal-source-detail")).toBeInTheDocument();
    });
    expect(api.kbChunksBySource).toHaveBeenCalledWith("src-42", 2000, 0);
  });

  it("W3-14: no page badge when evidence has no page", () => {
    setState({
      sources: [
        { title: "文献 B", identifier: "lit-2", source: "literature" },
      ] as never,
      selectedSources: ["lit-2"],
      kbIngest: {
        docs: [{ identifier: "lit-2", status: "indexed", source_id: "src-43", error: null }],
        done: 1,
        total: 1,
        indexed: 1,
        failed: 0,
        message: "完成",
        active: false,
      } as never,
    });
    render(<SourcesPanel />);
    expect(screen.getByText("文献 B")).toBeTruthy();
    expect(screen.queryByTestId("page-jump-lit-2")).toBeNull();
  });
});
