/**
 * W6-3 (P2-3): HubCollectionsPane 测试 —— mock collectionsApi：
 * 列表渲染、创建表单提交、刷新按钮、详情 snapshot diff 展示。
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { collectionsApi } from "../../api/domains/collections";
import { useStore } from "../../store";
import HubCollectionsPane from "./HubCollectionsPane";

vi.mock("../../api/domains/collections", () => ({
  collectionsApi: {
    list: vi.fn(),
    create: vi.fn(),
    detail: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    refresh: vi.fn(),
  },
}));

const list = vi.mocked(collectionsApi.list);
const create = vi.mocked(collectionsApi.create);
const detail = vi.mocked(collectionsApi.detail);
const refresh = vi.mocked(collectionsApi.refresh);

const SUMMARY = {
  collection_id: "col_1",
  name: "VIANT 防腐",
  query: "waterborne conversion coating",
  filters: { date_from: 2020 },
  screening_preset: null,
  schedule: { enabled: true, interval_hours: 24, last_run: 1700000000 },
  snapshot_count: 1,
  last_snapshot: {
    snapshot_id: "snap_1",
    at: 1700000000,
    total: 3,
    added: 2,
    removed: 1,
  },
};

const DETAIL = {
  ...SUMMARY,
  snapshots: [
    {
      snapshot_id: "snap_1",
      at: 1700000000,
      actor: "user",
      query: SUMMARY.query,
      filters: { date_from: 2020 },
      item_ids: ["id-a", "id-b", "id-c"],
      added: ["id-a", "id-b"],
      removed: ["id-old"],
      total: 3,
      manifest_added: 2,
      manifest_skipped_existing: 0,
    },
  ],
};

describe("HubCollectionsPane", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useStore.setState({ activeProjectId: "proj-1" } as never);
    list.mockResolvedValue({ collections: [SUMMARY] });
  });

  it("渲染集合列表与上次快照摘要", async () => {
    render(<HubCollectionsPane active />);
    await waitFor(() => expect(list).toHaveBeenCalledWith("proj-1"));
    expect(screen.getByTestId("hub-collection-col_1")).toBeTruthy();
    expect(screen.getByText("VIANT 防腐")).toBeTruthy();
    // 上次快照摘要：共 3 · +2 · −1
    expect(screen.getByText(/上次：共 3/)).toBeTruthy();
  });

  it("创建表单提交后列表新增一行", async () => {
    const user = userEvent.setup();
    create.mockResolvedValue({ ...DETAIL, collection_id: "col_2", name: "新集合", snapshots: [] });
    detail.mockResolvedValue({ ...DETAIL, collection_id: "col_2", name: "新集合", snapshots: [] });
    render(<HubCollectionsPane active />);
    await waitFor(() => expect(list).toHaveBeenCalled());

    await user.type(screen.getByTestId("hub-collections-name"), "新集合");
    await user.type(screen.getByTestId("hub-collections-query"), "zinc phosphate");
    await user.click(screen.getByTestId("hub-collections-create"));

    await waitFor(() => expect(create).toHaveBeenCalled());
    const payload = create.mock.calls[0][1];
    expect(payload.name).toBe("新集合");
    expect(payload.query).toBe("zinc phosphate");
    expect(screen.getByTestId("hub-collection-col_2")).toBeTruthy();
  });

  it("刷新按钮调用 refresh 并重载列表", async () => {
    const user = userEvent.setup();
    refresh.mockResolvedValue(DETAIL.snapshots[0]);
    render(<HubCollectionsPane active />);
    await waitFor(() => expect(list).toHaveBeenCalled());

    await user.click(screen.getByTestId("hub-collection-refresh-col_1"));
    await waitFor(() => expect(refresh).toHaveBeenCalledWith("proj-1", "col_1"));
    expect(list).toHaveBeenCalledTimes(2); // 初始 + 刷新后重载
  });

  it("打开详情展示 snapshot 的新增/移除 diff", async () => {
    const user = userEvent.setup();
    detail.mockResolvedValue(DETAIL);
    render(<HubCollectionsPane active />);
    await waitFor(() => expect(list).toHaveBeenCalled());

    await user.click(screen.getByTestId("hub-collection-open-col_1"));
    await waitFor(() => expect(detail).toHaveBeenCalledWith("proj-1", "col_1"));
    expect(screen.getByTestId("hub-snapshot-snap_1")).toBeTruthy();
    expect(screen.getByText(/id-a, id-b/)).toBeTruthy();
    expect(screen.getByText(/id-old/)).toBeTruthy();
  });

  it("API 失败时展示错误", async () => {
    list.mockRejectedValue(new Error("boom"));
    render(<HubCollectionsPane active />);
    await waitFor(() =>
      expect(screen.getByTestId("hub-collections-error")).toBeTruthy(),
    );
  });
});
