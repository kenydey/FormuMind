import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import SourceDetailModal from "./SourceDetailModal";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      kbChunksBySource: vi.fn(),
      kgLinkSource: vi.fn(),
    },
  };
});

const chunks = [
  { chunk_id: "c1", text: "第一页内容", page: 1 },
  { chunk_id: "c2", text: "第二页内容", page: 2 },
  { chunk_id: "c3", text: "第二页续", page: 2 },
];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("SourceDetailModal focusPage (W3-14)", () => {
  beforeEach(() => {
    vi.mocked(api.kbChunksBySource).mockReset();
    vi.mocked(api.kbChunksBySource).mockResolvedValue(chunks as never);
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("scrolls to the first chunk of focusPage after load", async () => {
    render(
      <SourceDetailModal
        title="Doc"
        sourceId="src-1"
        focusPage={2}
        onClose={() => {}}
      />
    );
    await waitFor(() => {
      expect(screen.getByTestId("source-chunk-1")).toBeInTheDocument();
    });
    // 组件内 60ms 延迟后执行滚动
    await sleep(200);
    const target = document.getElementById("source-chunk-2-1");
    expect(target).not.toBeNull();
    expect(target).toHaveTextContent("第二页内容");
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("renders chunks without focusPage (no scroll)", async () => {
    render(<SourceDetailModal title="Doc" sourceId="src-1" onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByTestId("source-chunk-0")).toBeInTheDocument();
    });
    await sleep(200);
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });
});

describe("SourceDetailModal 切块分页 (P-6)", () => {
  beforeEach(() => {
    vi.mocked(api.kbChunksBySource).mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  const many = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      chunk_id: `c${i}`,
      text: `内容${i}`,
      page: Math.floor(i / 10) + 1,
    }));

  it("120 个 chunk 只渲染首屏 50 个，可翻页", async () => {
    vi.mocked(api.kbChunksBySource).mockResolvedValue(many(120) as never);
    render(<SourceDetailModal title="Doc" sourceId="src-x" onClose={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId("source-chunk-0")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("source-chunk-49")).toBeInTheDocument();
    expect(screen.queryByTestId("source-chunk-50")).not.toBeInTheDocument();
    expect(screen.getByTestId("source-chunk-pager")).toHaveTextContent("第 1 / 3 页");
    fireEvent.click(screen.getByTestId("source-chunk-next"));
    await waitFor(() =>
      expect(screen.getByTestId("source-chunk-50")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("source-chunk-0")).not.toBeInTheDocument();
    expect(screen.getByTestId("source-chunk-pager")).toHaveTextContent("第 2 / 3 页");
  });

  it("focusPage 跳到目标 chunk 所在分页", async () => {
    vi.mocked(api.kbChunksBySource).mockResolvedValue(many(120) as never);
    render(
      <SourceDetailModal
        title="Doc"
        sourceId="src-x"
        focusPage={9}
        onClose={() => {}}
      />,
    );
    // focusPage=9 的首个 chunk 全局序号为 80 → 第 2 页
    await waitFor(() =>
      expect(screen.getByTestId("source-chunk-80")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("source-chunk-pager")).toHaveTextContent("第 2 / 3 页");
    expect(document.getElementById("source-chunk-9-80")).toBeTruthy();
  });
});
