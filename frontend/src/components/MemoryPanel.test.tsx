import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MemoryPanel from "./MemoryPanel";
import type { MemoryListResponse } from "../api";

const listMemories = vi.fn();
const deleteMemory = vi.fn();

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listMemories: (...a: unknown[]) => listMemories(...a),
      deleteMemory: (...a: unknown[]) => deleteMemory(...a),
    },
  };
});

function pageResponse(items: MemoryListResponse["items"], total?: number): MemoryListResponse {
  return { items, total: total ?? items.length, page: 1, page_size: 20 };
}

const SEED = [
  { id: 1, scope: "global", scope_id: "", key: "gk", value: "全局记忆", created_at: "2026-09-27T00:00:00", updated_at: "2026-09-27T00:00:00" },
  { id: 2, scope: "project", scope_id: "p1", key: "pk", value: "p1 偏好水性", created_at: "2026-09-27T00:00:00", updated_at: "2026-09-27T00:00:00" },
  { id: 3, scope: "user", scope_id: "u1", key: "uk", value: "u1 记录", created_at: "2026-09-27T00:00:00", updated_at: "2026-09-27T00:00:00" },
] as MemoryListResponse["items"];

describe("MemoryPanel", () => {
  beforeEach(() => {
    listMemories.mockReset();
    deleteMemory.mockReset();
    listMemories.mockResolvedValue(pageResponse(SEED));
    deleteMemory.mockResolvedValue({ ok: true, id: 2 });
    vi.stubGlobal("confirm", vi.fn(() => true));
  });

  it("renders memories grouped with scope badges", async () => {
    render(<MemoryPanel />);
    await waitFor(() => expect(listMemories).toHaveBeenCalled());
    expect(screen.getByText("gk")).toBeInTheDocument();
    expect(screen.getByText("p1 偏好水性")).toBeInTheDocument();
    // scope badges (scoped inside their list items to avoid the filter buttons)
    expect(within(screen.getByTestId("memory-item-1")).getByText("全局")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("memory-item-2")).getByText("项目 · p1")
    ).toBeInTheDocument();
  });

  it("scope filter reloads with the selected scope", async () => {
    render(<MemoryPanel />);
    await waitFor(() => expect(listMemories).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "项目" }));
    await waitFor(() =>
      expect(listMemories).toHaveBeenLastCalledWith(
        expect.objectContaining({ scope: "project", page: 1 })
      )
    );
  });

  it("search box reloads with q", async () => {
    render(<MemoryPanel />);
    await waitFor(() => expect(listMemories).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("搜索记忆"), { target: { value: "水性" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() =>
      expect(listMemories).toHaveBeenLastCalledWith(expect.objectContaining({ q: "水性" }))
    );
  });

  it("delete calls api then reloads the list", async () => {
    render(<MemoryPanel />);
    await waitFor(() => expect(listMemories).toHaveBeenCalled());
    const item = screen.getByTestId("memory-item-2");
    fireEvent.click(within(item).getByRole("button", { name: "删除记忆 pk" }));
    await waitFor(() => expect(deleteMemory).toHaveBeenCalledWith(2));
    await waitFor(() => expect(listMemories).toHaveBeenCalledTimes(2));
  });

  it("shows empty state when there are no memories", async () => {
    listMemories.mockResolvedValue(pageResponse([]));
    render(<MemoryPanel />);
    await waitFor(() => expect(screen.getByTestId("memory-empty")).toBeInTheDocument());
  });

  it("shows an error box when loading fails", async () => {
    listMemories.mockRejectedValue(new Error("boom"));
    render(<MemoryPanel />);
    await waitFor(() => expect(screen.getByText(/加载失败/)).toBeInTheDocument());
  });
});
