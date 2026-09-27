import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import LiteratureFreezeStrip from "./LiteratureFreezeStrip";

const capture = vi.fn();
const freeze = vi.fn();
const screenApi = vi.fn();
const getMan = vi.fn();

vi.mock("../../api", () => ({
  api: {
    getLiteratureManifest: (projectId: string) => getMan(projectId),
    captureLiteratureManifest: (body: unknown) => capture(body),
    freezeLiteratureManifest: (body: unknown) => freeze(body),
    unfreezeLiteratureManifest: vi.fn(async () => ({})),
    screenLiteratureManifest: (body: unknown) => screenApi(body),
  },
  formatApiError: (e: unknown) => String(e),
}));

describe("LiteratureFreezeStrip", () => {
  beforeEach(() => {
    capture.mockReset();
    freeze.mockReset();
    screenApi.mockReset();
    getMan.mockReset();
    capture.mockResolvedValue({});
    freeze.mockResolvedValue({});
    screenApi.mockResolvedValue({});
    getMan.mockResolvedValue({
      project_id: "p1",
      items: [{ id: "a", title: "Epoxy", screening: "unset" }],
      frozen: null,
      coverage: { candidate_count: 1, frozen_count: 0 },
    });
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
