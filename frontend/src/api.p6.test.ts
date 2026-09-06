import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("P6 workbench field-only HTTP clients", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("updateWorkbenchRowTags PUTs /tags with body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        id: 1,
        campaign_id: 9,
        status: "Pending",
        planned_params: {},
        actual_params: {},
        measurements: {},
        tags: ["urgent"],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const row = await api.updateWorkbenchRowTags(9, 1, ["urgent"]);
    expect(row.tags).toEqual(["urgent"]);
    expect(fetchMock).toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/experiments/workbench/9/rows/1/tags");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ tags: ["urgent"] });
  });

  it("updateWorkbenchRowNote PUTs /note with body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        id: 2,
        campaign_id: 9,
        status: "Pending",
        planned_params: {},
        actual_params: {},
        measurements: {},
        note: "hello",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const row = await api.updateWorkbenchRowNote(9, 2, "hello");
    expect(row.note).toBe("hello");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/experiments/workbench/9/rows/2/note");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ note: "hello" });
  });
});
