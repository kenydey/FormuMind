/**
 * `postDoeCyclePause` sends `ttlHours` only when the caller chose one; without it the server applies its default
 * (24 h, FORMUMIND_DOE_CYCLE_PAUSE_TTL_HOURS).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./http", async (importOriginal) => {
  const orig = await importOriginal<typeof import("./http")>();
  return { ...orig, post: vi.fn(), get: vi.fn() };
});

import { post } from "./http";
import { apiMethods } from "./methods";

const mockPost = vi.mocked(post);

beforeEach(() => {
  mockPost.mockReset();
  mockPost.mockResolvedValue({ status: "success", message: "ok" });
});

describe("api.postDoeCyclePause", () => {
  it("sends just the flag by default", async () => {
    await apiMethods.postDoeCyclePause(7, true);
    expect(mockPost).toHaveBeenCalledWith("/api/experiments/hooks/pause-doecycle/7", { isPaused: true });
  });

  it("sends the TTL when one is chosen, including 0 (until resumed)", async () => {
    await apiMethods.postDoeCyclePause(7, true, { ttlHours: 6 });
    expect(mockPost).toHaveBeenLastCalledWith("/api/experiments/hooks/pause-doecycle/7", { isPaused: true, ttlHours: 6 });
    await apiMethods.postDoeCyclePause("7", true, { ttlHours: 0 });
    expect(mockPost).toHaveBeenLastCalledWith("/api/experiments/hooks/pause-doecycle/7", { isPaused: true, ttlHours: 0 });
  });

  it("resuming sends no TTL", async () => {
    await apiMethods.postDoeCyclePause(7, false);
    expect(mockPost).toHaveBeenCalledWith("/api/experiments/hooks/pause-doecycle/7", { isPaused: false });
  });
});
