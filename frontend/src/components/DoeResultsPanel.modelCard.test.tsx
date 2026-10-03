import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import DoeResultsPanel from "./DoeResultsPanel";

const model = {
  domain: "anticorrosion_coating",
  project_id: "proj-1",
  metric: "salt_spray_hours",
  backend: "sklearn-rf",
  n_samples: 24,
  r2: 0.93,
  cv_r2: 0.8,
  rmse: 12.3,
  version_id: "v1",
  pinned: true,
  newer_version_id: "v2",
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "modelVersions").mockResolvedValue([
    { version_id: "v2", is_current: false, trained_at: "2026-10-03T08:00:00+00:00", n_samples: 24 },
    { version_id: "v1", is_current: true, pinned: true, trained_at: "2026-10-02T08:00:00+00:00", n_samples: 12 },
  ] as never);
  vi.spyOn(api, "getEnvFlags").mockResolvedValue({ flags: [] } as never);
  useStore.setState({ models: [model], doePlan: null, modelHistory: [] } as never);
});

describe("DoeResultsPanel model cards", () => {
  it("shows the lock on a pinned model and opens its version list", async () => {
    render(<DoeResultsPanel />);
    const card = await screen.findByTestId("model-card-salt_spray_hours");
    expect(card.querySelector('[data-testid="model-pin-badge"]')).toBeTruthy();
    expect(screen.getByTestId("model-versions-toggle-salt_spray_hours").textContent).toContain("•"); // newer version waiting

    fireEvent.click(screen.getByTestId("model-versions-toggle-salt_spray_hours"));
    await waitFor(() => expect(api.modelVersions).toHaveBeenCalledWith("proj-1", "salt_spray_hours"));
    expect(await screen.findByTestId("model-version-v2")).toBeTruthy();
    expect(screen.getByTestId("model-pinned-note")).toBeTruthy();
  });
});
