/**
 * P1-32 on the audit page: ``GET /api/reports/checklist/{run_id}`` had a route, a service and a
 * report appendix, but no way to look at it. The audit modal now generates the checklist for the
 * selected run — and must never show run A's checklist while run B is selected.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { reviewsApi, type ReviewChecklist } from "../api";
import ReviewerAuditModal from "./ReviewerAuditModal";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    reviewsApi: {
      getReviewRun: vi.fn(),
      listReviewRuns: vi.fn(),
      rerunReviewRun: vi.fn(),
      getReviewChecklist: vi.fn(),
    },
  };
});

const getReviewRun = vi.mocked(reviewsApi.getReviewRun);
const listReviewRuns = vi.mocked(reviewsApi.listReviewRuns);
const getReviewChecklist = vi.mocked(reviewsApi.getReviewChecklist);

function detail(runId: string) {
  return {
    run_id: runId,
    session_key: `k-${runId}`,
    project_id: "proj-1",
    status: "complete",
    outcome: "pass",
    stale: false,
    stale_reason: null,
    warn_count: 0,
    fail_count: 0,
    unaddressed_count: 0,
    started_at: 1727400000,
    finished_at: 1727400100,
    dispositions: {},
  };
}

function checklistFor(runId: string, statements: string[]): ReviewChecklist {
  return {
    run_id: runId,
    session_key: `k-${runId}`,
    outcome: "pass",
    generated_at: 1727400200,
    items: statements.map((statement, i) => ({
      id: `chk-${runId}-${i}`,
      category: i === 0 ? "citation" : "numeric",
      statement,
      verdict: i === 0 ? "pass" : "flagged",
      evidence_refs: [],
      reviewer_note: "",
    })),
    summary: { total: statements.length, pass: 1, flagged: statements.length - 1, n_a: 0 },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  getReviewRun.mockImplementation(async (id: string) => detail(id));
  listReviewRuns.mockResolvedValue({
    items: [
      { run_id: "r1", outcome: "pass", warn_count: 0, fail_count: 0 },
      { run_id: "r2", outcome: "pass", warn_count: 0, fail_count: 0 },
    ],
  });
});

function open() {
  render(<ReviewerAuditModal open onClose={() => {}} initialRunId="r1" projectId="proj-1" />);
}

describe("ReviewerAuditModal checklist", () => {
  it("generates the checklist of the selected run on demand", async () => {
    getReviewChecklist.mockResolvedValue(checklistFor("r1", ["引用 [^1] 与来源一致", "配比 40% 与表 2 不符"]));
    open();

    const button = await screen.findByTestId("reviewer-checklist-load");
    expect(getReviewChecklist).not.toHaveBeenCalled(); // not generated just by opening the page
    fireEvent.click(button);

    await waitFor(() => expect(screen.getAllByTestId("reviewer-checklist-item")).toHaveLength(2));
    expect(getReviewChecklist).toHaveBeenCalledWith("r1");
    expect(screen.getByTestId("reviewer-checklist-summary").textContent).toMatch(/通过 1.*标记 1.*不适用 0/);
    expect(screen.getByText("配比 40% 与表 2 不符")).toBeTruthy();
    expect(screen.getByTestId("reviewer-checklist-load").textContent).toBe("刷新");
  });

  it("says so when a run has nothing to check", async () => {
    getReviewChecklist.mockResolvedValue({
      ...checklistFor("r1", []),
      summary: { total: 0, pass: 0, flagged: 0, n_a: 0 },
    });
    open();
    fireEvent.click(await screen.findByTestId("reviewer-checklist-load"));
    expect(await screen.findByText(/没有可核对的条目/)).toBeTruthy();
  });

  it("shows the failure instead of an empty checklist", async () => {
    getReviewChecklist.mockImplementation(async () => {
      throw new Error("review run not found: r1");
    });
    open();
    fireEvent.click(await screen.findByTestId("reviewer-checklist-load"));
    expect(await screen.findByText(/review run not found/)).toBeTruthy();
    expect(screen.queryByTestId("reviewer-checklist-item")).toBeNull();
  });

  it("drops run A's checklist when run B is selected", async () => {
    getReviewChecklist.mockResolvedValue(checklistFor("r1", ["只属于 r1 的条目"]));
    open();
    fireEvent.click(await screen.findByTestId("reviewer-checklist-load"));
    await screen.findByText("只属于 r1 的条目");

    fireEvent.click((await screen.findAllByTestId("reviewer-audit-row"))[1]);

    await waitFor(() => expect(screen.queryByText("只属于 r1 的条目")).toBeNull());
    expect(screen.getByTestId("reviewer-checklist-load").textContent).toBe("生成清单");
  });

  it("ignores a slow checklist that arrives after another run was selected", async () => {
    let release: (c: ReviewChecklist) => void = () => {};
    getReviewChecklist.mockImplementation(
      () => new Promise<ReviewChecklist>((resolve) => {
        release = resolve;
      }),
    );
    open();
    fireEvent.click(await screen.findByTestId("reviewer-checklist-load"));
    await waitFor(() => expect(getReviewChecklist).toHaveBeenCalledWith("r1"));

    fireEvent.click((await screen.findAllByTestId("reviewer-audit-row"))[1]);
    await waitFor(() => expect(getReviewRun).toHaveBeenCalledWith("r2"));
    release(checklistFor("r1", ["迟到的 r1 清单"]));

    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("迟到的 r1 清单")).toBeNull();
    expect(screen.getByTestId("reviewer-checklist-load").textContent).toBe("生成清单");
  });
});
