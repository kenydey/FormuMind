/**
 * The backend had versions / rollback endpoints but the UI never called them — and a
 * rollback was silently overwritten by the next retrain. The panel lists versions, rolls
 * back (which pins) with a confirmation, and releases the pin.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ModelInfo } from "../api";
import ModelVersionsPanel from "./ModelVersionsPanel";

const modelVersions = vi.fn();
const rollbackModel = vi.fn();
const unpinModel = vi.fn();

vi.mock("../api", () => ({
  formatApiError: (e: unknown) => (e instanceof Error ? e.message : String(e)),
  api: {
    modelVersions: (...a: unknown[]) => modelVersions(...a),
    rollbackModel: (...a: unknown[]) => rollbackModel(...a),
    unpinModel: (...a: unknown[]) => unpinModel(...a),
  },
}));

const base: ModelInfo = {
  domain: "anticorrosion_coating" as never,
  project_id: "proj-1",
  metric: "salt_spray_hours",
  backend: "sklearn-rf",
  n_samples: 24,
  r2: 0.93,
  cv_r2: 0.8,
  rmse: 12.3,
  version_id: "v2",
};

const rows = [
  { version_id: "v2", is_current: true, pinned: false, trained_at: "2026-10-03T08:00:00+00:00", backend: "sklearn-rf", n_samples: 24, r2: 0.93, rmse: 12.3 },
  { version_id: "v1", is_current: false, pinned: false, trained_at: "2026-10-02T08:00:00+00:00", backend: "sklearn-rf", n_samples: 12, r2: 0.88, rmse: 20.1 },
];

beforeEach(() => {
  modelVersions.mockReset();
  rollbackModel.mockReset();
  unpinModel.mockReset();
  modelVersions.mockResolvedValue(rows);
  rollbackModel.mockResolvedValue({});
  unpinModel.mockResolvedValue({});
});

describe("ModelVersionsPanel", () => {
  it("lists the versions of this metric and marks the served one", async () => {
    render(<ModelVersionsPanel model={base} />);
    await screen.findByTestId("model-version-v1");
    expect(modelVersions).toHaveBeenCalledWith("proj-1", "salt_spray_hours");
    expect(screen.getByTestId("model-version-v2").textContent).toContain("当前");
    expect(screen.getByTestId("model-version-v1").textContent).toContain("n=12");
    // the served version cannot be rolled back to
    expect(screen.queryByTestId("model-rollback-v2")).toBeNull();
    expect(screen.getByTestId("model-rollback-v1")).toBeTruthy();
  });

  it("asks for confirmation, then rolls back, tells the host and reloads", async () => {
    const onChanged = vi.fn();
    render(<ModelVersionsPanel model={base} onChanged={onChanged} />);
    fireEvent.click(await screen.findByTestId("model-rollback-v1"));
    expect(rollbackModel).not.toHaveBeenCalled(); // not before the confirmation
    expect(screen.getByText(/回滚后将锁定该版本/)).toBeTruthy();

    fireEvent.click(screen.getByTestId("model-rollback-confirm-v1"));
    await waitFor(() => expect(rollbackModel).toHaveBeenCalledWith("proj-1", "salt_spray_hours", "v1"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(modelVersions.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("cancelling the confirmation does nothing", async () => {
    render(<ModelVersionsPanel model={base} />);
    fireEvent.click(await screen.findByTestId("model-rollback-v1"));
    fireEvent.click(screen.getByText("取消"));
    expect(rollbackModel).not.toHaveBeenCalled();
    expect(screen.getByTestId("model-rollback-v1")).toBeTruthy();
  });

  it("shows the lock, explains that new versions are archived, and releases it", async () => {
    const pinned: ModelInfo = { ...base, version_id: "v1", pinned: true, newer_version_id: "v2" };
    modelVersions.mockResolvedValue([
      { ...rows[0], is_current: false },
      { ...rows[1], is_current: true, pinned: true },
    ]);
    const onChanged = vi.fn();
    render(<ModelVersionsPanel model={pinned} onChanged={onChanged} />);

    expect(await screen.findByTestId("model-pinned-note")).toHaveTextContent(/已锁定/);
    expect(screen.getByTestId("model-pinned-note")).toHaveTextContent(/新版本已存档/);
    expect(screen.getByTestId("model-version-v2").textContent).toContain("最新");

    fireEvent.click(screen.getByTestId("model-unpin"));
    await waitFor(() => expect(unpinModel).toHaveBeenCalledWith("proj-1", "salt_spray_hours"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  });

  it("shows why an action failed and keeps the list", async () => {
    rollbackModel.mockRejectedValue(new Error("model version not found"));
    render(<ModelVersionsPanel model={base} />);
    fireEvent.click(await screen.findByTestId("model-rollback-v1"));
    fireEvent.click(screen.getByTestId("model-rollback-confirm-v1"));
    expect(await screen.findByTestId("model-versions-error")).toHaveTextContent(/model version not found/);
    expect(screen.getByTestId("model-version-v1")).toBeTruthy();
  });

  it("explains itself instead of calling the API when the model has no project", () => {
    render(<ModelVersionsPanel model={{ ...base, project_id: undefined }} />);
    expect(screen.getByTestId("model-versions-unavailable")).toBeTruthy();
    expect(modelVersions).not.toHaveBeenCalled();
  });

  it("ignores a slow answer for a previous request", async () => {
    let releaseFirst!: (v: unknown) => void;
    modelVersions.mockImplementationOnce(
      () =>
        new Promise((res) => {
          releaseFirst = res;
        }),
    );
    const { rerender } = render(<ModelVersionsPanel model={base} />);
    // the served version changed (e.g. a retrain) → a second, faster request
    modelVersions.mockResolvedValueOnce([{ ...rows[1], version_id: "fresh", is_current: true }]);
    rerender(<ModelVersionsPanel model={{ ...base, version_id: "fresh" }} />);
    await screen.findByTestId("model-version-fresh");
    releaseFirst(rows);
    await Promise.resolve();
    expect(screen.queryByTestId("model-version-v2")).toBeNull();
  });
});
