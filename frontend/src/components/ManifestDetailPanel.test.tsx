import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ManifestDetailPanel, {
  computeManifestCoverage,
  buildManifestMarkdown,
  formatLocator,
  type ManifestItem,
} from "./ManifestDetailPanel";

const getMan = vi.fn();
const saveExport = vi.fn();
const setLoc = vi.fn();

vi.mock("../api", () => ({
  api: {
    getLiteratureManifest: (projectId: string) => getMan(projectId),
    setLiteratureItemLocator: (itemId: string, body: unknown) => setLoc(itemId, body),
    kbChunksBySource: vi.fn(async () => []),
    getSourceTables: vi.fn(async () => ({ tables: [] })),
    kgLinkSource: vi.fn(async () => ({})),
    saveProjectExport: (projectId: string, filename: string, content: string) =>
      saveExport(projectId, filename, content),
  },
  formatApiError: (e: unknown) => String(e),
}));

vi.mock("../utils/export", () => ({
  // 透传到被 mock 的 api.saveProjectExport，保持与真实实现一致的调用链
  saveTextToProjectShelf: (projectId: string, filename: string, content: string) =>
    saveExport(projectId, filename, content),
  shelfFilename: (prefix: string, ext: string) => `${prefix}_2026-09-28.${ext}`,
}));

const ITEMS: ManifestItem[] = [
  {
    id: "s1",
    title: "Epoxy coating corrosion",
    doi: "10.1/abc",
    screening: "match",
    snippet: "abstract one",
    source: "project_source",
    has_fulltext: true,
  },
  {
    id: "s2",
    title: "Primer adhesion",
    doi: null,
    screening: "no_match",
    snippet: "",
    source: "search_hit",
  },
  {
    id: "s3",
    title: "Topcoat weathering",
    doi: "10.2/def",
    screening: "unset",
    snippet: "abstract three",
    enrich_status: "fetched",
  },
];

const FROZEN_MANIFEST = {
  project_id: "p1",
  items: ITEMS,
  frozen: {
    at: 1727000000,
    actor: "hub",
    item_ids: ["s1", "s2", "s3"],
    digest: "abcdef1234567890",
  },
  coverage: { candidate_count: 3, frozen_count: 3 },
};

describe("ManifestDetailPanel", () => {
  beforeEach(() => {
    getMan.mockReset();
    saveExport.mockReset();
    getMan.mockResolvedValue(FROZEN_MANIFEST);
    saveExport.mockResolvedValue(undefined);
  });

  it("无 projectId 时显示占位", () => {
    render(<ManifestDetailPanel projectId={null} />);
    expect(screen.getByTestId("manifest-detail-panel").textContent).toMatch(/请先选择活动项目/);
    expect(getMan).not.toHaveBeenCalled();
  });

  it("七格统计正确（冻结 corpus）", async () => {
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-coverage"));
    const cells = screen.getByTestId("manifest-coverage");
    // 冻结条目 3 · 有 DOI 2 · 有摘要 2 · 有全文 2 · 命中 1 · 排除 1 · 待筛 1
    const text = cells.textContent ?? "";
    expect(text).toMatch(/冻结条目/);
    const values = Array.from(
      cells.querySelectorAll('[data-testid^="manifest-coverage-cell-"]'),
    ).map((el) => el.textContent);
    expect(values).toHaveLength(7);
    expect(values[0]).toMatch(/3/);
    expect(values[1]).toMatch(/2/); // 有 DOI
    expect(values[2]).toMatch(/2/); // 有摘要
    expect(values[3]).toMatch(/2/); // 有全文
    expect(values[4]).toMatch(/1/); // 筛选命中
    expect(values[5]).toMatch(/1/); // 筛选排除
    expect(values[6]).toMatch(/1/); // 待筛选
    // 诚实声明
    expect(screen.getByTestId("manifest-honesty").textContent).toMatch(/不是人工已逐篇读完/);
    // 冻结元信息
    expect(screen.getByTestId("manifest-frozen-meta").textContent).toMatch(/已冻结/);
  });

  it("点击条目打开 SourceDetailModal 详情弹窗", async () => {
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-item-s1"));
    fireEvent.click(screen.getByTestId("manifest-item-open-s1"));
    await waitFor(() => {
      expect(screen.getByTestId("modal-source-detail")).toBeTruthy();
    });
  });

  it("换样式切换预览", async () => {
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-style-preview"));
    fireEvent.click(screen.getByTestId("manifest-style-citation"));
    await waitFor(() => {
      expect(screen.getByTestId("manifest-style-preview").textContent).toMatch(
        /\[1\] Epoxy coating corrosion/,
      );
    });
    fireEvent.click(screen.getByTestId("manifest-style-table"));
    await waitFor(() => {
      expect(
        screen.getByTestId("manifest-style-preview").querySelector("table"),
      ).toBeTruthy();
    });
  });

  it("存新版调用 shelf 保存（不修改 manifest 本体）", async () => {
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-save-btn"));
    fireEvent.click(screen.getByTestId("manifest-save-btn"));
    await waitFor(() => expect(saveExport).toHaveBeenCalledTimes(1));
    const [pid, filename, content] = saveExport.mock.calls[0];
    expect(pid).toBe("p1");
    expect(filename).toMatch(/^manifest_.*\.md$/);
    expect(content).toContain("Epoxy coating corrosion");
    await waitFor(() => {
      expect(screen.getByTestId("manifest-save-msg").textContent).toMatch(/已存新版/);
    });
  });

  it("未冻结时显示候选集统计", async () => {
    getMan.mockResolvedValue({
      project_id: "p1",
      items: ITEMS.slice(0, 1),
      frozen: null,
      coverage: { candidate_count: 1, frozen_count: 0 },
    });
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-coverage"));
    expect(screen.getByTestId("manifest-frozen-meta").textContent).toMatch(/未冻结/);
    expect(screen.getByTestId("manifest-coverage").textContent).toMatch(/候选条目/);
  });
});

describe("computeManifestCoverage", () => {
  it("空列表七格全 0", () => {
    const cells = computeManifestCoverage([], true);
    expect(cells).toHaveLength(7);
    expect(cells.every((c) => c.value === 0)).toBe(true);
  });

  it("待筛选 = 总数 − 命中 − 排除", () => {
    const cells = computeManifestCoverage(ITEMS, false);
    const byLabel = Object.fromEntries(cells.map((c) => [c.label, c.value]));
    expect(byLabel["候选条目"]).toBe(3);
    expect(byLabel["待筛选"]).toBe(1);
  });
});

describe("formatLocator", () => {
  it("组合 page/figure/table", () => {
    expect(formatLocator({ id: "x", locator: { page: 3, figure: "2" } })).toBe("p.3 / Fig.2");
    expect(formatLocator({ id: "x", locator: { table: "1" } })).toBe("Tab.1");
    expect(formatLocator({ id: "x", locator: { page: 5, figure: "2", table: "1" } })).toBe(
      "p.5 / Fig.2 / Tab.1",
    );
  });

  it("无 locator 返回空串", () => {
    expect(formatLocator({ id: "x" })).toBe("");
    expect(formatLocator({ id: "x", locator: {} })).toBe("");
  });
});

describe("ManifestDetailPanel locator 展示", () => {
  beforeEach(() => {
    getMan.mockReset();
    getMan.mockResolvedValue({
      project_id: "p1",
      items: [
        { id: "s9", title: "Loc paper", doi: "10.9/z", screening: "match", locator: { page: 3, figure: "2" } },
      ],
      frozen: { at: 1727000000, actor: "hub", item_ids: ["s9"], digest: "d9" },
      coverage: { candidate_count: 1, frozen_count: 1 },
    });
  });

  it("引用行展示 locator", async () => {
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-item-locator-s9"));
    expect(screen.getByTestId("manifest-item-locator-s9").textContent).toBe("p.3 / Fig.2");
  });

  it("标注定位器：表单预填现值，保存走 setLiteratureItemLocator 并重载", async () => {
    setLoc.mockReset();
    setLoc.mockResolvedValue({});
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-item-locator-edit-s9"));
    expect(screen.queryByTestId("manifest-locator-form-s9")).toBeNull();

    fireEvent.click(screen.getByTestId("manifest-item-locator-edit-s9"));
    expect((screen.getByTestId("manifest-locator-page") as HTMLInputElement).value).toBe("3");
    expect((screen.getByTestId("manifest-locator-figure") as HTMLInputElement).value).toBe("2");

    fireEvent.change(screen.getByTestId("manifest-locator-page"), { target: { value: "7" } });
    fireEvent.change(screen.getByTestId("manifest-locator-table"), { target: { value: " 1 " } });
    fireEvent.click(screen.getByTestId("manifest-locator-save"));

    await waitFor(() => expect(setLoc).toHaveBeenCalledTimes(1));
    expect(setLoc).toHaveBeenCalledWith("s9", {
      project_id: "p1",
      page: 7,
      figure: "2",
      table: "1",
    });
    // 保存后表单收起并重载 manifest（初次加载 + 保存后一次）。
    await waitFor(() => expect(screen.queryByTestId("manifest-locator-form-s9")).toBeNull());
    expect(getMan).toHaveBeenCalledTimes(2);
  });

  it("三项全空 = 清除定位（后端收到全 null）", async () => {
    setLoc.mockReset();
    setLoc.mockResolvedValue({});
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-item-locator-edit-s9"));
    fireEvent.click(screen.getByTestId("manifest-item-locator-edit-s9"));
    fireEvent.change(screen.getByTestId("manifest-locator-page"), { target: { value: "" } });
    fireEvent.change(screen.getByTestId("manifest-locator-figure"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("manifest-locator-save"));

    await waitFor(() => expect(setLoc).toHaveBeenCalledTimes(1));
    expect(setLoc).toHaveBeenCalledWith("s9", {
      project_id: "p1",
      page: null,
      figure: null,
      table: null,
    });
  });

  it("非法页码在前端拦截，不发请求", async () => {
    setLoc.mockReset();
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-item-locator-edit-s9"));
    fireEvent.click(screen.getByTestId("manifest-item-locator-edit-s9"));
    fireEvent.change(screen.getByTestId("manifest-locator-page"), { target: { value: "0" } });
    fireEvent.click(screen.getByTestId("manifest-locator-save"));

    await waitFor(() => screen.getByTestId("manifest-error"));
    expect(screen.getByTestId("manifest-error").textContent).toMatch(/页码/);
    expect(setLoc).not.toHaveBeenCalled();
  });

  it("取消编辑不写入", async () => {
    setLoc.mockReset();
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-item-locator-edit-s9"));
    fireEvent.click(screen.getByTestId("manifest-item-locator-edit-s9"));
    fireEvent.click(screen.getByTestId("manifest-locator-cancel"));
    expect(screen.queryByTestId("manifest-locator-form-s9")).toBeNull();
    expect(setLoc).not.toHaveBeenCalled();
  });
});

describe("buildManifestMarkdown", () => {
  it("citation 样式输出编号引用", () => {
    const md = buildManifestMarkdown("citation", "快照", ITEMS);
    expect(md).toContain("[1] Epoxy coating corrosion. DOI: 10.1/abc");
    expect(md).toContain("[2] Primer adhesion");
  });

  it("table 样式输出 markdown 表格", () => {
    const md = buildManifestMarkdown("table", "快照", ITEMS);
    expect(md).toContain("| # | 标题 | DOI |");
    expect(md).toContain("10.1/abc");
  });

  it("list 样式输出分节", () => {
    const md = buildManifestMarkdown("list", "快照", ITEMS);
    expect(md).toContain("## 1. Epoxy coating corrosion");
    expect(md).toContain("> abstract one");
  });
});

describe("ManifestDetailPanel 回归（F-3 / F-4）", () => {
  beforeEach(() => {
    getMan.mockReset();
    saveExport.mockReset();
  });

  it("F-3: frozen 缺 item_ids（旧数据/损坏）不白屏，引用列表为空", async () => {
    getMan.mockResolvedValue({
      project_id: "p1",
      items: ITEMS,
      frozen: { at: 1727000000, actor: "hub", digest: "d9" }, // 无 item_ids
      coverage: { candidate_count: 3, frozen_count: 0 },
    });
    render(<ManifestDetailPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("manifest-coverage"));
    // 不白屏；item_ids 缺失 → 冻结范围为空
    expect(screen.getByTestId("manifest-item-list").textContent).toMatch(/暂无条目/);
  });

  it("F-4: project 快速切换时旧 manifest 不覆盖新数据", async () => {
    let release!: (v: unknown) => void;
    const slow = new Promise((res) => {
      release = res;
    });
    getMan.mockImplementationOnce(() => slow);
    getMan.mockResolvedValue({
      project_id: "p2",
      items: [],
      frozen: null,
      coverage: { candidate_count: 0, frozen_count: 0 },
    });
    const { rerender } = render(<ManifestDetailPanel projectId="p1" />);
    rerender(<ManifestDetailPanel projectId="p2" />);
    await waitFor(() => screen.getByTestId("manifest-coverage"));
    release({
      project_id: "p1",
      items: ITEMS,
      frozen: null,
      coverage: { candidate_count: 3, frozen_count: 0 },
    });
    await new Promise((r) => setTimeout(r, 20));
    // 迟到的 p1 数据不得覆盖 p2
    expect(screen.getByTestId("manifest-item-list").textContent).toMatch(/暂无条目/);
  });
});
