import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import LiteratureFreezeStrip from "./LiteratureFreezeStrip";

const capture = vi.fn(async () => ({}));
const freeze = vi.fn(async () => ({}));
const screenApi = vi.fn(async () => ({}));
const getMan = vi.fn(async () => ({
  project_id: "p1",
  items: [{ id: "a", title: "Epoxy", screening: "unset" }],
  frozen: null,
  coverage: { candidate_count: 1, frozen_count: 0 },
}));

vi.mock("../../api", () => ({
  api: {
    getLiteratureManifest: (...a: unknown[]) => getMan(...a),
    captureLiteratureManifest: (...a: unknown[]) => capture(...a),
    freezeLiteratureManifest: (...a: unknown[]) => freeze(...a),
    unfreezeLiteratureManifest: vi.fn(async () => ({})),
    screenLiteratureManifest: (...a: unknown[]) => screenApi(...a),
  },
  formatApiError: (e: unknown) => String(e),
}));

describe("LiteratureFreezeStrip", () => {
  beforeEach(() => {
    capture.mockClear();
    freeze.mockClear();
    screenApi.mockClear();
    getMan.mockClear();
  });

  it("loads stats and capture/freeze", async () => {
    render(<LiteratureFreezeStrip projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByTestId("literature-freeze-stats").textContent).toMatch(/候选 1/);
    });
    fireEvent.click(screen.getByTestId("literature-capture-btn"));
    await waitFor(() => expect(capture).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("literature-freeze-btn"));
    await waitFor(() => expect(freeze).toHaveBeenCalled());
  });

  it("runs screening form", async () => {
    render(<LiteratureFreezeStrip projectId="p1" />);
    await waitFor(() => screen.getByTestId("literature-screen-toggle"));
    fireEvent.click(screen.getByTestId("literature-screen-toggle"));
    fireEvent.click(screen.getByTestId("literature-screen-btn"));
    await waitFor(() => expect(screenApi).toHaveBeenCalled());
  });
});
