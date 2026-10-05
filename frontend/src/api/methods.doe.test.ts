/**
 * `api.doe` carries the CCD axial distance to POST /api/doe as `ccd_alpha`; without one the URL is unchanged,
 * so the backend default (face-centred: every run inside the factor ranges) applies.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./http", async (importOriginal) => {
  const orig = await importOriginal<typeof import("./http")>();
  return { ...orig, post: vi.fn() };
});

import { post } from "./http";
import { apiMethods } from "./methods";

const mockPost = vi.mocked(post);
const requirement = { domain: "anticorrosion_coating" } as never;

beforeEach(() => {
  mockPost.mockReset();
  mockPost.mockResolvedValue({ design: "ccd", factors: [], runs: [], notes: "" });
});

describe("api.doe ccd_alpha", () => {
  it("sends nothing extra by default", async () => {
    await apiMethods.doe(requirement, "ccd");
    expect(mockPost).toHaveBeenCalledWith("/api/doe?design=ccd&engine=auto", requirement);
  });

  it("sends the rotatable request", async () => {
    await apiMethods.doe(requirement, "ccd", "native", { ccdAlpha: "rotatable" });
    expect(mockPost).toHaveBeenCalledWith("/api/doe?design=ccd&engine=native&ccd_alpha=rotatable", requirement);
  });

  it("sends an explicit number, encoded", async () => {
    await apiMethods.doe(requirement, "ccd", "auto", { ccdAlpha: 1.414 });
    expect(mockPost.mock.calls[0][0]).toBe("/api/doe?design=ccd&engine=auto&ccd_alpha=1.414");
  });

  it("an undefined alpha is the same as none", async () => {
    await apiMethods.doe(requirement, "ccd", "auto", { ccdAlpha: undefined });
    expect(mockPost.mock.calls[0][0]).toBe("/api/doe?design=ccd&engine=auto");
  });
});
