/**
 * The rollback buttons act on the *displayed* version numbers with the current projectId.
 * A slow answer for the project that was just switched away from must never replace the new
 * project's list — it would offer "roll back to v7" of project A on project B's payload.
 */
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectHistoryPanel from "./ProjectHistoryPanel";

const getProjectHistory = vi.fn();

vi.mock("../api", () => ({
  formatApiError: (e: unknown) => (e instanceof Error ? e.message : String(e)),
  api: {
    getProjectHistory: (...a: unknown[]) => getProjectHistory(...a),
    rollbackProject: vi.fn(),
  },
}));
vi.mock("../store", () => ({
  useStore: (sel: (s: unknown) => unknown) => sel({ loadProject: vi.fn() }),
}));

const version = (n: number, cause: string) => ({
  version: n,
  cause,
  created_at: "2026-10-03T06:00:00",
  fields: [],
  chat_count: 0,
  source_count: 0,
});

beforeEach(() => {
  getProjectHistory.mockReset();
});

describe("ProjectHistoryPanel", () => {
  it("keeps showing the new project's versions when the old project's answer arrives late", async () => {
    let releaseA!: (v: unknown) => void;
    getProjectHistory.mockImplementation((id: string) =>
      id === "A"
        ? new Promise((res) => {
            releaseA = res;
          })
        : Promise.resolve({ project_id: "B", versions: [version(2, "cause-of-B")] }),
    );
    const { rerender } = render(<ProjectHistoryPanel projectId="A" />);
    rerender(<ProjectHistoryPanel projectId="B" />);
    await screen.findByText("cause-of-B");

    await act(async () => {
      releaseA({ project_id: "A", versions: [version(7, "cause-of-A")] });
    });
    expect(screen.queryByText("cause-of-A")).toBeNull();
    expect(screen.getByText("cause-of-B")).toBeTruthy();
  });

  it("empties the list while the next project's history is loading", async () => {
    let releaseB!: (v: unknown) => void;
    getProjectHistory.mockImplementation((id: string) =>
      id === "A"
        ? Promise.resolve({ project_id: "A", versions: [version(7, "cause-of-A")] })
        : new Promise((res) => {
            releaseB = res;
          }),
    );
    const { rerender } = render(<ProjectHistoryPanel projectId="A" />);
    await screen.findByText("cause-of-A");
    rerender(<ProjectHistoryPanel projectId="B" />);
    expect(screen.queryByText("cause-of-A")).toBeNull();
    await act(async () => {
      releaseB({ project_id: "B", versions: [] });
    });
  });
});
