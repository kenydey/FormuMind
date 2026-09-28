/**
 * F-2: ReviewerAuditModal 请求参数断言 —— projectId 必须透传进
 * listReviewRuns 请求（后端 GET /api/reviews/runs fail-closed，无
 * project_id 时 400 拒绝）。
 *
 * 卡片→弹窗的 prop 透传另见 ReviewerCard.test.tsx（"F-2: 打开审计弹窗时透传
 * projectId"）；这里断言弹窗→API 的请求参数，闭合整条链。
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { reviewsApi } from "../api";
import ReviewerAuditModal from "./ReviewerAuditModal";

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

const listReviewRuns = vi.mocked(reviewsApi.listReviewRuns);
const getReviewRun = vi.mocked(reviewsApi.getReviewRun);

const DETAIL = {
  run_id: "r1",
  session_key: "k1",
  project_id: "proj-7",
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

beforeEach(() => {
  vi.clearAllMocks();
  getReviewRun.mockResolvedValue({ ...DETAIL });
  listReviewRuns.mockResolvedValue({ items: [] });
});

describe("ReviewerAuditModal F-2", () => {
  it("loadList 把 projectId 带进请求参数", async () => {
    render(
      <ReviewerAuditModal
        open
        onClose={() => {}}
        initialRunId="r1"
        projectId="proj-7"
      />,
    );
    await waitFor(() => expect(listReviewRuns).toHaveBeenCalled());
    expect(listReviewRuns).toHaveBeenCalledWith(
      expect.objectContaining({ projectId: "proj-7", limit: 30 }),
    );
    // 弹窗正常渲染（无白屏/无报错）
    expect(screen.getByTestId("reviewer-audit-modal")).toBeInTheDocument();
  });

  it("无 projectId 时请求不带 project_id（后端 400 显式报错，不静默越界）", async () => {
    render(
      <ReviewerAuditModal
        open
        onClose={() => {}}
        initialRunId="r1"
        projectId={null}
      />,
    );
    await waitFor(() => expect(listReviewRuns).toHaveBeenCalled());
    const params = listReviewRuns.mock.calls[0][0] as
      | { projectId?: string }
      | undefined;
    expect(params?.projectId).toBeUndefined();
  });
});
