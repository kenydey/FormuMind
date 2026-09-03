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
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: { ...actual.api, searchStream: mocks.searchStream },
    awaitTaskStream: mocks.awaitTaskStream,
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
