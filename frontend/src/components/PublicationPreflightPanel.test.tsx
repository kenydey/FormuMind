import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import PublicationPreflightPanel, { severityTone, sortFindings } from "./PublicationPreflightPanel";
import type { PreflightFinding, PreflightState } from "../api";

const getState = vi.fn();
const review = vi.fn();
const override = vi.fn();
const resolve = vi.fn();
const finalize = vi.fn();

vi.mock("../api", () => {
  class ApiError extends Error {
    status?: number;
    detail?: unknown;
    constructor(message: string, opts?: { detail?: unknown; status?: number }) {
      super(message);
      this.detail = opts?.detail;
      this.status = opts?.status;
    }
  }
  return {
    ApiError,
    formatApiError: (e: unknown) => (e instanceof Error ? e.message : String(e)),
    api: {
      getPreflightState: (p: string, k: string) => getState(p, k),
      reviewPreflight: (b: unknown) => review(b),
      overridePreflightFinding: (b: unknown) => override(b),
      resolvePreflightFinding: (b: unknown) => resolve(b),
      finalizePreflight: (b: unknown) => finalize(b),
    },
  };
});

const f = (over: Partial<PreflightFinding>): PreflightFinding => ({
  id: "f1",
  check: "citation",
  severity: "blocking",
  status: "open",
  title: "引用 [^7] 未绑定",
  detail: "正文引用了不存在的脚注",
  evidence: ["…claim [^7]…"],
  ...over,
});

const state = (over: Partial<PreflightState> = {}): PreflightState => ({
  project_id: "p1",
  kind: "storm",
  content_hash: "abc123",
  findings: [f({})],
  finalization: null,
  open_blocking: 1,
  open_major: 0,
  ready: false,
  ...over,
});

beforeEach(() => {
  for (const m of [getState, review, override, resolve, finalize]) m.mockReset();
});

describe("PublicationPreflightPanel helpers", () => {
  it("sorts open blocking first, handled last", () => {
    const sorted = sortFindings([
      f({ id: "a", severity: "minor", status: "open" }),
      f({ id: "b", severity: "blocking", status: "overridden" }),
      f({ id: "c", severity: "blocking", status: "open" }),
      f({ id: "d", severity: "major", status: "open" }),
    ]);
    expect(sorted.map((x) => x.id)).toEqual(["c", "d", "a", "b"]);
  });

  it("colours severities", () => {
    expect(severityTone("blocking")).toContain("rose");
    expect(severityTone("major")).toContain("amber");
    expect(severityTone("info")).toContain("slate");
  });
});

describe("PublicationPreflightPanel", () => {
  it("asks for a project first", () => {
    render(<PublicationPreflightPanel projectId={null} />);
    expect(screen.getByTestId("preflight-panel").textContent).toMatch(/请先选择活动项目/);
    expect(getState).not.toHaveBeenCalled();
  });

  it("lists findings and says exports are blocked", async () => {
    getState.mockResolvedValue(state());
    render(<PublicationPreflightPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("preflight-finding-f1"));
    expect(getState).toHaveBeenCalledWith("p1", "storm");
    expect(screen.getByTestId("preflight-summary").textContent).toMatch(/1 条阻断项未处理/);
    expect(screen.getByTestId("preflight-status-f1").textContent).toBe("待处理");
    expect((screen.getByTestId("preflight-finalize-btn") as HTMLButtonElement).disabled).toBe(true);
  });

  it("explains an un-reviewed project instead of showing an empty list", async () => {
    getState.mockResolvedValue(state({ content_hash: "", findings: [], open_blocking: 0 }));
    render(<PublicationPreflightPanel projectId="p1" />);
    await waitFor(() => expect(getState).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId("preflight-summary").textContent).toMatch(/尚未预检/));
    expect((screen.getByTestId("preflight-finalize-btn") as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders nothing, and loads nothing, when the Settings toggle is off", () => {
    getState.mockResolvedValue(state());
    render(<PublicationPreflightPanel projectId="p1" enabled={false} />);
    expect(screen.queryByTestId("preflight-panel")).toBeNull();
    expect(getState).not.toHaveBeenCalled();
  });

  it("resolve: needs a note, then calls the API and shows the handled state", async () => {
    getState.mockResolvedValue(state());
    resolve.mockResolvedValue(
      state({
        findings: [f({ status: "resolved", resolution: { kind: "resolved", actor: "user", note: "已改" } })],
        open_blocking: 0,
      }),
    );
    render(<PublicationPreflightPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("preflight-resolve-f1"));
    fireEvent.click(screen.getByTestId("preflight-resolve-f1"));

    fireEvent.click(screen.getByTestId("preflight-action-submit"));
    expect(screen.getByTestId("preflight-error").textContent).toMatch(/处理备注/);
    expect(resolve).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("preflight-action-text"), { target: { value: "已改" } });
    fireEvent.click(screen.getByTestId("preflight-action-submit"));

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1));
    expect(resolve).toHaveBeenCalledWith({
      project_id: "p1",
      kind: "storm",
      finding_id: "f1",
      actor: "user",
      note: "已改",
    });
    await waitFor(() => expect(screen.getByTestId("preflight-status-f1").textContent).toBe("已处理"));
    expect(screen.queryByTestId("preflight-edit-f1")).toBeNull();
  });

  it("override: sends actor + reason (the audit trail)", async () => {
    getState.mockResolvedValue(state());
    override.mockResolvedValue(state({ findings: [f({ status: "overridden" })], open_blocking: 0 }));
    render(<PublicationPreflightPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("preflight-override-f1"));
    fireEvent.click(screen.getByTestId("preflight-override-f1"));
    fireEvent.change(screen.getByTestId("preflight-action-actor"), { target: { value: "alice" } });
    fireEvent.change(screen.getByTestId("preflight-action-text"), { target: { value: "草稿阶段接受" } });
    fireEvent.click(screen.getByTestId("preflight-action-submit"));

    await waitFor(() => expect(override).toHaveBeenCalledTimes(1));
    expect(override).toHaveBeenCalledWith({
      project_id: "p1",
      kind: "storm",
      finding_id: "f1",
      actor: "alice",
      reason: "草稿阶段接受",
    });
    await waitFor(() => expect(screen.getByTestId("preflight-status-f1").textContent).toBe("已放行"));
  });

  it("cancel closes the form without calling anything", async () => {
    getState.mockResolvedValue(state());
    render(<PublicationPreflightPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("preflight-override-f1"));
    fireEvent.click(screen.getByTestId("preflight-override-f1"));
    fireEvent.click(screen.getByTestId("preflight-action-cancel"));
    expect(screen.queryByTestId("preflight-edit-f1")).toBeNull();
    expect(override).not.toHaveBeenCalled();
    expect(resolve).not.toHaveBeenCalled();
  });

  it("re-review is STORM-only and switching kind reloads that kind's state", async () => {
    getState.mockResolvedValue(state());
    review.mockResolvedValue(state({ findings: [], open_blocking: 0 }));
    render(<PublicationPreflightPanel projectId="p1" />);
    await waitFor(() => screen.getByTestId("preflight-review-btn"));

    fireEvent.click(screen.getByTestId("preflight-review-btn"));
    await waitFor(() => expect(review).toHaveBeenCalledWith({ project_id: "p1", kind: "storm" }));

    fireEvent.change(screen.getByTestId("preflight-kind"), { target: { value: "tech_report_doe" } });
    await waitFor(() => expect(getState).toHaveBeenLastCalledWith("p1", "tech_report_doe"));
    expect(screen.queryByTestId("preflight-review-btn")).toBeNull();
  });

  it("finalize succeeds once nothing blocking is open", async () => {
    getState.mockResolvedValue(state({ findings: [f({ status: "resolved" })], open_blocking: 0 }));
    finalize.mockResolvedValue({
      ok: true,
      ready: true,
      errors: [],
      state: state({
        findings: [f({ status: "resolved" })],
        open_blocking: 0,
        ready: true,
        finalization: { actor: "user", at: 1727000000, artifactHash: "abc123" },
      }),
    });
    render(<PublicationPreflightPanel projectId="p1" />);
    await waitFor(() => expect((screen.getByTestId("preflight-finalize-btn") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId("preflight-finalize-btn"));
    await waitFor(() => expect(finalize).toHaveBeenCalledWith({ project_id: "p1", kind: "storm", actor: "user" }));
    await waitFor(() => expect(screen.getByTestId("preflight-summary").textContent).toMatch(/已定稿/));
  });

  it("shows the reasons of a refused finalize instead of raw JSON", async () => {
    const { ApiError } = await import("../api");
    getState.mockResolvedValue(state({ findings: [f({ status: "resolved" })], open_blocking: 0 }));
    finalize.mockRejectedValue(
      new ApiError("{...}", { status: 409, detail: { ok: false, ready: false, errors: ["内容 hash 已变，请重新 review"] } }),
    );
    render(<PublicationPreflightPanel projectId="p1" />);
    await waitFor(() => expect((screen.getByTestId("preflight-finalize-btn") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId("preflight-finalize-btn"));
    await waitFor(() => screen.getByTestId("preflight-error"));
    expect(screen.getByTestId("preflight-error").textContent).toBe("内容 hash 已变，请重新 review");
  });
});
