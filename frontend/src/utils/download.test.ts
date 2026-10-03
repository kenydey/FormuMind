import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { downloadWithAuth, filenameFromDisposition } from "./download";
import { setApiToken } from "../api/http";

describe("filenameFromDisposition", () => {
  it("reads plain, quoted and RFC 5987 forms", () => {
    expect(filenameFromDisposition('attachment; filename="plan.csv"')).toBe("plan.csv");
    expect(filenameFromDisposition("attachment; filename=plan.csv")).toBe("plan.csv");
    expect(filenameFromDisposition("attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.pdf")).toBe("报告.pdf");
    expect(filenameFromDisposition(null)).toBeNull();
    expect(filenameFromDisposition("inline")).toBeNull();
  });
});

describe("downloadWithAuth", () => {
  let clicked: Array<{ href: string; download: string }>;
  const fetchMock = vi.fn();

  beforeEach(() => {
    clicked = [];
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:fake"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ href: this.href, download: this.download });
    });
    localStorage.clear();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("sends the bearer token (a plain <a href> cannot) and names the file from the response", async () => {
    setApiToken("secret-token");
    fetchMock.mockResolvedValue(
      new Response("a,b\n1,2\n", { status: 200, headers: { "Content-Disposition": 'attachment; filename="doe_1.csv"' } }),
    );

    const name = await downloadWithAuth("/api/doe/1/export?format=csv", "fallback.csv");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/doe/1/export?format=csv");
    expect((init as RequestInit).headers).toEqual({ Authorization: "Bearer secret-token" });
    expect(name).toBe("doe_1.csv");
    expect(clicked).toEqual([{ href: "blob:fake", download: "doe_1.csv" }]);
    // Revoked later, not while the browser may still be reading the blob.
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(40_000);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:fake");
  });

  it("sends no Authorization header when auth is off, and falls back to the given name", async () => {
    fetchMock.mockResolvedValue(new Response("x", { status: 200 }));
    const name = await downloadWithAuth("/api/materials/template", "materials_template.csv");
    expect((fetchMock.mock.calls[0][1] as RequestInit).headers).toEqual({});
    expect(name).toBe("materials_template.csv");
  });

  it("raises the API error (and never starts a download) on a non-2xx answer", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Unauthorized" }), { status: 401, headers: { "Content-Type": "application/json" } }),
    );
    await expect(downloadWithAuth("/api/x", "f")).rejects.toThrow(/Unauthorized/);
    expect(clicked).toEqual([]);
  });
});
