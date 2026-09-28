/**
 * W5-4 (P1-28): ReviewerCard 测试 —— mock reviewsApi：
 * 计数渲染、stale 提示、重审调用。
 */
import { act, render, screen, waitFor } from "@testing-library/react";
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

const auditModalProps = vi.fn();
vi.mock("./ReviewerAuditModal", () => ({
  default: (props: Record<string, unknown>) => {
    auditModalProps(props);
    return <div data-testid="mock-audit-modal" />;
  },
}));

describe("ReviewerCard 回归（F-2 / F-4 / F-9 / F-11）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getReviewRun.mockResolvedValue({ ...RUN });
    auditModalProps.mockClear();
  });

  it("F-9: 重审返回 rounds=0（无 run_id）时提示已是最新", async () => {
    rerunReviewRun.mockResolvedValue({
      review: { status: "pass" },
      fix: { rounds: 0 },
      final_answer: "a.",
    });
    const user = userEvent.setup();
    render(
      <ReviewerCard runId="key-1-123" question="q?" answer="a." citations={[]} />,
    );
    await waitFor(() => expect(getReviewRun).toHaveBeenCalledTimes(1));
    await user.click(screen.getByTestId("reviewer-card-rerun"));
    const notice = await screen.findByTestId("reviewer-card-notice");
    expect(notice).toHaveTextContent("已是最新，无需重审");
    // 无新 run，不切换 activeRunId，只重载旧 run
    expect(getReviewRun).toHaveBeenLastCalledWith("key-1-123");
  });

  it("F-11: question 为空时重审按钮禁用（防后端 400）", async () => {
    render(<ReviewerCard runId="key-1-123" question="" answer="a." />);
    await waitFor(() => expect(getReviewRun).toHaveBeenCalled());
    expect(screen.getByTestId("reviewer-card-rerun")).toBeDisabled();
    expect(rerunReviewRun).not.toHaveBeenCalled();
  });

  it("F-2: 打开审计弹窗时透传 projectId", async () => {
    const user = userEvent.setup();
    render(
      <ReviewerCard runId="key-1-123" question="q?" answer="a." citations={[]} />,
    );
    await waitFor(() => expect(getReviewRun).toHaveBeenCalled());
    await user.click(screen.getByTestId("reviewer-card-audit"));
    await waitFor(() => expect(auditModalProps).toHaveBeenCalled());
    expect(auditModalProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ projectId: "proj-1" }),
    );
  });

  it("F-4: runId 快速切换时旧请求不覆盖新数据", async () => {
    const slow = new Promise((res) => setTimeout(() => res({ ...RUN, run_id: "slow", warn_count: 99 }), 50));
    getReviewRun.mockImplementationOnce(() => slow as never);
    getReviewRun.mockResolvedValueOnce({ ...RUN, run_id: "fast", warn_count: 7 });
    const { rerender } = render(
      <ReviewerCard runId="slow" question="q?" answer="a." />,
    );
    rerender(<ReviewerCard runId="fast" question="q?" answer="a." />);
    await waitFor(() =>
      expect(screen.getByTestId("reviewer-card-warn")).toHaveTextContent("7"),
    );
    // slow 迟到返回时不得覆盖 fast 的数据
    await slow;
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId("reviewer-card-warn")).toHaveTextContent("7");
  });

  it("F-4: 重审耗时中 runId 切换，重审的旧结果不覆盖新数据", async () => {
    let resolveRerun!: (v: unknown) => void;
    rerunReviewRun.mockImplementation(
      () =>
        new Promise<unknown>((res) => {
          resolveRerun = res;
        }) as never,
    );
    getReviewRun.mockImplementation((id: string) =>
      Promise.resolve(
        id === "run-b"
          ? { ...RUN, run_id: "run-b", warn_count: 5 }
          : { ...RUN, run_id: id, warn_count: 2 },
      ) as never,
    );
    const user = userEvent.setup();
    const { rerender } = render(
      <ReviewerCard runId="run-a" question="q?" answer="a." citations={[]} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("reviewer-card-warn")).toHaveTextContent("2"),
    );
    await user.click(screen.getByTestId("reviewer-card-rerun")); // 重审挂起
    rerender(<ReviewerCard runId="run-b" question="q?" answer="a." />); // 新回答到达
    await waitFor(() =>
      expect(screen.getByTestId("reviewer-card-warn")).toHaveTextContent("5"),
    );
    await act(async () => {
      resolveRerun({
        review: { status: "pass" },
        fix: { run_id: "stale-new-run", rounds: 1 },
        final_answer: "a.",
      });
    });
    // 旧重审结果被丢弃：仍显示 run-b 的数据，且不得再加载旧 run
    expect(screen.getByTestId("reviewer-card-warn")).toHaveTextContent("5");
    expect(getReviewRun).not.toHaveBeenCalledWith("stale-new-run");
  });
});
