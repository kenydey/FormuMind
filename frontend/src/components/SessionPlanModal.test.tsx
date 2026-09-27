import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../api";
import SessionPlanModal from "./SessionPlanModal";

vi.mock("../api", () => ({
  api: { getSessionPlan: vi.fn(), decideSessionPlan: vi.fn() },
  formatApiError: (e: unknown) => (e instanceof Error ? e.message : String(e)),
}));

const getSessionPlan = api.getSessionPlan as Mock;
const decideSessionPlan = api.decideSessionPlan as Mock;

const pendingPlan = {
  plan_id: "plan-1",
  session_id: "sess-1",
  status: "pending",
  phases: [
    {
      name: "DOE 设计",
      steps: [
        { desc: "选因子", status: "pending" },
        { desc: "生成矩阵", status: "pending" },
      ],
    },
    { name: "执行", steps: [{ desc: "跑实验", status: "pending" }] },
  ],
  created_at: "2026-09-27T10:00:00",
  decided_at: null,
  decided_by: null,
};

describe("SessionPlanModal (W3-9)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  it("shows approve/reject buttons for a pending plan and requires confirmation", async () => {
    getSessionPlan.mockResolvedValue(pendingPlan);
    decideSessionPlan.mockResolvedValue({ ...pendingPlan, status: "approved" });

    render(<SessionPlanModal planId="plan-1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("plan-status")).toBeTruthy());
    expect(screen.getByTestId("plan-status").textContent).toContain("待审批");
    expect(screen.getByText(/DOE 设计/)).toBeTruthy();
    expect(screen.getByText(/选因子/)).toBeTruthy();

    // approve requires a second confirmation
    fireEvent.click(screen.getByTestId("plan-approve-btn"));
    expect(screen.getByTestId("plan-confirm-box").textContent).toContain("不可逆");
    expect(decideSessionPlan).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("plan-confirm-btn"));
    await waitFor(() => expect(decideSessionPlan).toHaveBeenCalledWith("plan-1", true));
    await waitFor(() =>
      expect(screen.getByTestId("plan-status").textContent).toContain("已批准")
    );
  });

  it("cancel dismisses the confirmation without calling decide", async () => {
    getSessionPlan.mockResolvedValue(pendingPlan);
    render(<SessionPlanModal planId="plan-1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("plan-reject-btn")).toBeTruthy());

    fireEvent.click(screen.getByTestId("plan-reject-btn"));
    fireEvent.click(screen.getByTestId("plan-cancel-btn"));
    expect(decideSessionPlan).not.toHaveBeenCalled();
    expect(screen.queryByTestId("plan-confirm-box")).toBeNull();
  });

  it("hides approve/reject for an already-decided plan", async () => {
    getSessionPlan.mockResolvedValue({ ...pendingPlan, status: "rejected" });
    render(<SessionPlanModal planId="plan-1" onClose={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId("plan-status").textContent).toContain("已拒绝")
    );
    expect(screen.queryByTestId("plan-approve-btn")).toBeNull();
    expect(screen.queryByTestId("plan-reject-btn")).toBeNull();
    expect(screen.getByText(/审批不可逆/)).toBeTruthy();
  });
});
