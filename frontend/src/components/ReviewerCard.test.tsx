/**
 * W5-4 (P1-28): ReviewerCard 测试 —— mock reviewsApi：
 * 计数渲染、stale 提示、重审调用。
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { reviewsApi } from "../api";
import ReviewerCard from "./ReviewerCard";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    reviewsApi: {
      getReviewRun: vi.fn(),
      listReviewRuns: vi.fn(),
      rerunReviewRun: vi.fn(),
    },
  };
});

const getReviewRun = vi.mocked(reviewsApi.getReviewRun);
const rerunReviewRun = vi.mocked(reviewsApi.rerunReviewRun);

const RUN = {
  run_id: "key-1-123",
  session_key: "key-1",
  project_id: "proj-1",
  status: "complete",
  outcome: "flagged",
  stale: false,
  stale_reason: null,
  warn_count: 2,
  fail_count: 1,
  unaddressed_count: 1,
  started_at: 1727400000,
  finished_at: 1727400100,
};

beforeEach(() => {
  vi.clearAllMocks();
  getReviewRun.mockResolvedValue({ ...RUN });
});

describe("ReviewerCard", () => {
  it("渲染 warn/fail 计数", async () => {
    render(
      <ReviewerCard runId="key-1-123" question="q?" answer="a." />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("reviewer-card-warn")).toHaveTextContent("2"),
    );
    expect(screen.getByTestId("reviewer-card-fail")).toHaveTextContent("1");
    expect(screen.getByTestId("reviewer-card")).toHaveTextContent("有发现");
    expect(getReviewRun).toHaveBeenCalledWith("key-1-123");
  });

  it("stale=true 显示过期提示与原因", async () => {
    getReviewRun.mockResolvedValue({
      ...RUN,
      stale: true,
      stale_reason: "artifact content changed",
    });
    render(
      <ReviewerCard runId="key-1-123" question="q?" answer="a." />,
    );
    const banner = await screen.findByTestId("reviewer-card-stale");
    expect(banner).toHaveTextContent("已过期");
    expect(banner).toHaveTextContent("artifact content changed");
  });

  it('stale="unverified" 显示未验证提示', async () => {
    getReviewRun.mockResolvedValue({
      ...RUN,
      stale: "unverified",
      stale_reason: "scope inputs not provided",
    });
    render(
      <ReviewerCard runId="key-1-123" question="q?" answer="a." />,
    );
    const banner = await screen.findByTestId("reviewer-card-stale");
    expect(banner).toHaveTextContent("未验证");
  });

  it("重审按钮调用 rerunReviewRun 并切换到新 run", async () => {
    rerunReviewRun.mockResolvedValue({
      review: { status: "pass" },
      fix: { run_id: "new-run-9", rounds: 0 },
      final_answer: "a.",
    });
    const user = userEvent.setup();
    render(
      <ReviewerCard runId="key-1-123" question="q?" answer="a." citations={[]} />,
    );
    await waitFor(() => expect(getReviewRun).toHaveBeenCalledTimes(1));
    await user.click(screen.getByTestId("reviewer-card-rerun"));
    await waitFor(() => expect(rerunReviewRun).toHaveBeenCalledTimes(1));
    expect(rerunReviewRun).toHaveBeenCalledWith(
      "key-1-123",
      expect.objectContaining({ question: "q?", answer: "a." }),
    );
    // 重审后加载新 run
    await waitFor(() =>
      expect(getReviewRun).toHaveBeenLastCalledWith("new-run-9"),
    );
  });

  it("加载失败时 fail-open 显示不可用提示", async () => {
    getReviewRun.mockRejectedValue(new Error("boom"));
    render(
      <ReviewerCard runId="key-1-123" question="q?" answer="a." />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("reviewer-card")).toHaveTextContent("不可用"),
    );
  });
});
