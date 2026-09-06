import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import RagPrewarmBar from "./RagPrewarmBar";

describe("RagPrewarmBar", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows prewarm button when idle", async () => {
    vi.spyOn(api, "getRagStatus").mockResolvedValue({
      backend: "colbert",
      formulation_mode: "rag",
      gpu_enabled: false,
      gpu_available: false,
      rag_backend_setting: "auto",
      prewarm: { status: "idle", backend: null, elapsed_ms: null, error: null },
    });
    render(<RagPrewarmBar />);
    expect(await screen.findByTestId("rag-prewarm-bar")).toBeTruthy();
    expect(screen.getByTestId("rag-prewarm-btn")).toBeTruthy();
  });

  it("calls prewarmRag when button clicked", async () => {
    vi.spyOn(api, "getRagStatus").mockResolvedValue({
      backend: "colbert",
      formulation_mode: "rag",
      gpu_enabled: false,
      gpu_available: false,
      rag_backend_setting: "auto",
      prewarm: { status: "failed", backend: null, elapsed_ms: null, error: "boom" },
    });
    const prewarm = vi.spyOn(api, "prewarmRag").mockResolvedValue({
      status: "warming",
      backend: "colbert",
      elapsed_ms: null,
      error: null,
    });
    render(<RagPrewarmBar />);
    await screen.findByTestId("rag-prewarm-btn");
    await userEvent.click(screen.getByTestId("rag-prewarm-btn"));
    await waitFor(() => expect(prewarm).toHaveBeenCalledWith(true));
  });
});
