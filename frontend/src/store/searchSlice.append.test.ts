/**
 * `searchSources` append semantics.
 *
 * Two behaviours are pinned here because they are the whole point of the
 * 「添加数据源」 feature and easy to regress:
 *
 *  - `append: true` keeps the sources already loaded and *adds* the new
 *    keyword's results (de-duplicated by identifier/title in `addSources`).
 *  - the default (no `append`) still clears first — that is the left column's
 *    「开始检索」 "change the topic, start over" behaviour.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  searchStream: vi.fn(),
  awaitTaskStream: vi.fn(),
  search: vi.fn(),
  probeBackend: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      searchStream: mocks.searchStream,
      search: mocks.search,
    },
    awaitTaskStream: mocks.awaitTaskStream,
    probeBackend: mocks.probeBackend,
  };
});

import { useStore } from "./index";

const EXISTING = { source: "patents", identifier: "old-1", title: "已有专利" };
const NEW = { source: "literature", identifier: "new-1", title: "新文献" };

function seed() {
  useStore.setState({
    searchQuery: "研究主题",
    sourceTypes: ["literature", "internet"],
    sources: [EXISTING] as never,
    selectedSources: ["old-1"],
    searchBusy: false,
    error: null,
  } as never);
}

function finishWithEvidence(evidence: unknown[]) {
  mocks.awaitTaskStream.mockResolvedValue({
    status: "COMPLETED",
    message: "done",
    data: { evidence, source_status: {} },
  } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.searchStream.mockResolvedValue({ task_id: "t1" } as never);
  // Default: the API is up, so a broken stream is not mislabelled as an
  // unreachable backend. Tests that need a down backend override this.
  mocks.probeBackend.mockResolvedValue(true);
  seed();
});

describe("searchSources append", () => {
  it("append=true 累加：保留现有数据源并追加新结果", async () => {
    finishWithEvidence([NEW]);

    await useStore.getState().searchSources("新关键词", { append: true });

    const { sources } = useStore.getState();
    expect(sources.map((s) => s.identifier)).toEqual(["old-1", "new-1"]);
  });

  it("默认（无 append）清空重搜：仅保留新结果", async () => {
    finishWithEvidence([NEW]);

    await useStore.getState().searchSources("新关键词");

    const { sources } = useStore.getState();
    expect(sources.map((s) => s.identifier)).toEqual(["new-1"]);
  });

  it("append=true 不覆盖研究主题 searchQuery", async () => {
    finishWithEvidence([NEW]);

    await useStore.getState().searchSources("新关键词", { append: true });

    expect(useStore.getState().searchQuery).toBe("研究主题");
  });
});

describe("searchSources stream→sync fallback", () => {
  it("stream 失败后走同步 api.search，并填充 sources", async () => {
    mocks.awaitTaskStream.mockRejectedValue(new Error("503: Redis broker unavailable"));
    mocks.search.mockResolvedValue({
      evidence: [NEW],
      total: 1,
      source_status: { literature: { available: true } },
      used_seed_fallback: false,
      filter_report: null,
    } as never);

    await useStore.getState().searchSources("新关键词");

    expect(mocks.search).toHaveBeenCalled();
    const { sources, selectedSources, error, searchBusy } = useStore.getState();
    expect(sources.map((s) => s.identifier)).toEqual(["new-1"]);
    expect(selectedSources).toContain("new-1");
    expect(searchBusy).toBe(false);
    expect(error).toMatch(/流式检索失败.*同步检索并成功/);
    expect(error).toContain("503");
  });

  it("后端在线时不得把流式中断说成「不可达」", async () => {
    mocks.awaitTaskStream.mockRejectedValue(new Error("Failed to fetch"));
    mocks.search.mockRejectedValue(new Error("Failed to fetch"));
    mocks.probeBackend.mockResolvedValue(true);

    await useStore.getState().searchSources("新关键词");

    const { error } = useStore.getState();
    expect(error).not.toContain("后端不可达");
    expect(error).toMatch(/同步检索也失败/);
  });

  it("两次探活都失败才报「后端不可达」", async () => {
    mocks.awaitTaskStream.mockRejectedValue(new Error("Failed to fetch"));
    mocks.search.mockRejectedValue(new Error("Failed to fetch"));
    mocks.probeBackend.mockResolvedValue(false);

    await useStore.getState().searchSources("新关键词");

    expect(useStore.getState().error).toContain("后端不可达");
  });

  it("流式中断但后端已恢复：同步检索成功文案改为「后端已恢复」", async () => {
    mocks.awaitTaskStream.mockRejectedValue(new Error("Failed to fetch"));
    mocks.search.mockResolvedValue({
      evidence: [NEW],
      total: 1,
      source_status: {},
      used_seed_fallback: false,
      filter_report: null,
    } as never);
    // 第一次探活（流式失败后）说不可达，第二次（同步成功后回读）不需要
    mocks.probeBackend.mockResolvedValueOnce(false);

    await useStore.getState().searchSources("新关键词");

    const { error, sources } = useStore.getState();
    expect(sources.map((s) => s.identifier)).toEqual(["new-1"]);
    expect(error).toMatch(/后端已恢复，同步检索成功/);
  });
});
