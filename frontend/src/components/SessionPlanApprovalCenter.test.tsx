import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { api } from "../api";
import SessionPlanApprovalCenter from "./SessionPlanApprovalCenter";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listPendingSessionPlans: vi.fn(),
      getSessionPlan: vi.fn(),
      decideSessionPlan: vi.fn(),
    },
  };
});

const PLAN_ITEM = {
  plan_id: "plan-abc",
  session_id: "sess-1",
  created_at: 1727.0,
  phase_names: ["design", "execute"],
  step_count: 3,
};

const PLAN_DETAIL = {
  plan_id: "plan-abc",
  session_id: "sess-1",
  status: "pending",
  phases: [
    { name: "design", steps: [{ desc: "define factors", status: "pending" }] },
  ],
  created_at: "2026-09-28T00:00:00",
  decided_at: null,
  decided_by: null,
};

function mockPending(items: typeof PLAN_ITEM[]) {
  vi.mocked(api.listPendingSessionPlans).mockResolvedValue({ items, total: items.length });
}

describe("SessionPlanApprovalCenter (W3-9 wiring)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api.listPendingSessionPlans).mockReset();
    vi.mocked(api.getSessionPlan).mockReset();
    vi.mocked(api.decideSessionPlan).mockReset();
    vi.mocked(api.getSessionPlan).mockResolvedValue(PLAN_DETAIL as never);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("opens the modal when a pending plan exists", async () => {
    mockPending([PLAN_ITEM]);
    render(<SessionPlanApprovalCenter />);
    await vi.advanceTimersByTimeAsync(10);
    await act(async () => {});
    expect(screen.getByTestId("session-plan-modal")).toBeInTheDocument();
    expect(api.getSessionPlan).toHaveBeenCalledWith("plan-abc");
  });

  it("renders nothing when no plan is pending", async () => {
    mockPending([]);
    const { container } = render(<SessionPlanApprovalCenter />);
    await vi.advanceTimersByTimeAsync(10);
    await act(async () => {});
    expect(container).toBeEmptyDOMElement();
  });

  it("closes the modal after the plan leaves the pending list", async () => {
    mockPending([PLAN_ITEM]);
    render(<SessionPlanApprovalCenter />);
    await vi.advanceTimersByTimeAsync(10);
    await act(async () => {});
    expect(screen.getByTestId("session-plan-modal")).toBeInTheDocument();
    // 审批后（或别处已决策）不再 pending：下一次轮询关闭弹窗
    mockPending([]);
    await vi.advanceTimersByTimeAsync(5000);
    await act(async () => {});
    expect(screen.queryByTestId("session-plan-modal")).not.toBeInTheDocument();
  });

  it("approving through the modal calls decide and the center closes on next poll", async () => {
    mockPending([PLAN_ITEM]);
    vi.mocked(api.decideSessionPlan).mockResolvedValue({
      ...PLAN_DETAIL,
      status: "approved",
    } as never);
    render(<SessionPlanApprovalCenter />);
    await vi.advanceTimersByTimeAsync(10);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("plan-approve-btn"));
    await act(async () => {});
    fireEvent.click(screen.getByTestId("plan-confirm-btn"));
    await act(async () => {});
    expect(api.decideSessionPlan).toHaveBeenCalledWith("plan-abc", true);
    mockPending([]);
    await vi.advanceTimersByTimeAsync(5000);
    await act(async () => {});
    expect(screen.queryByTestId("session-plan-modal")).not.toBeInTheDocument();
  });
});
