/**
 * Wrappers must unwrap the envelopes the backend really sends (round-4).
 *
 * `substructureSearch` / `scaffoldSubstitutes` / `kbChunksBySource` declared bare arrays while the
 * endpoints answer `{ …, hits }` / `{ chunks }`. Component tests mocked the wrapper and returned
 * arrays — exactly what production never did — so the first real structure search crashed the
 * Materials panel (`structHits.map is not a function`). These tests feed the wrappers the payloads the
 * backend produces (the Python side pins the same shapes in tests/test_response_envelopes.py).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./http", async (importOriginal) => {
  const orig = await importOriginal<typeof import("./http")>();
  return { ...orig, get: vi.fn() };
});

import { get } from "./http";
import { apiMethods } from "./methods";

const mockGet = vi.mocked(get);

beforeEach(() => mockGet.mockReset());

describe("response envelopes", () => {
  it("substructureSearch returns the hits, not the envelope", async () => {
    mockGet.mockResolvedValueOnce({ smarts: "[NX3;H2]", hits: [{ name: "IPDA" }, { name: "MDA" }] });
    const hits = await apiMethods.substructureSearch("[NX3;H2]");
    expect(Array.isArray(hits)).toBe(true);
    expect(hits.map((h) => h.name)).toEqual(["IPDA", "MDA"]);
    expect(String(mockGet.mock.calls[0][0])).toMatch(/^\/api\/chemical\/substructure\?/);
  });

  it("scaffoldSubstitutes returns the hits, not the envelope", async () => {
    mockGet.mockResolvedValueOnce({ smiles: "CCO", hits: [{ name: "Ethanol", reason: "same scaffold" }] });
    const hits = await apiMethods.scaffoldSubstitutes("CCO");
    expect(hits).toEqual([{ name: "Ethanol", reason: "same scaffold" }]);
  });

  it("an envelope without hits is an empty list, never undefined", async () => {
    mockGet.mockResolvedValueOnce({ smarts: "x" });
    expect(await apiMethods.substructureSearch("x")).toEqual([]);
  });

  it("kbChunksBySource returns the chunk page", async () => {
    mockGet.mockResolvedValueOnce({ chunks: [{ id: "c1", ord: 0 }, { id: "c2", ord: 1 }] });
    const chunks = await apiMethods.kbChunksBySource("src-1", 2000, 0);
    expect(chunks.map((c) => c.id)).toEqual(["c1", "c2"]);
    expect(String(mockGet.mock.calls[0][0])).toBe("/api/kb/chunks/by-source/src-1?limit=2000&offset=0");
  });
});

describe("attachment uploads", () => {
  const sent: { url: string; body: FormData }[] = [];

  beforeEach(() => {
    sent.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: { body: FormData }) => {
        sent.push({ url, body: init.body });
        return new Response(JSON.stringify({ id: "a1" }), { status: 200 });
      })
    );
  });

  it("sends kind and note as query parameters — the backend ignores them in the form body", async () => {
    const file = new File(["x"], "report.pdf");
    await apiMethods.uploadAttachment(file, 7, { kind: "microscope", note: "批次 3" });
    const url = new URL(sent[0].url, "http://localhost");
    expect(url.pathname).toBe("/api/experiments/7/attachments");
    expect(url.searchParams.get("kind")).toBe("microscope");
    expect(url.searchParams.get("note")).toBe("批次 3");
    expect([...sent[0].body.keys()]).toEqual(["file"]);
  });

  it("adds no query string when nothing was chosen", async () => {
    await apiMethods.uploadWorkbenchAttachment(new File(["x"], "a.png"), 2, 9);
    expect(sent[0].url).toBe("/api/experiments/workbench/2/rows/9/attachments");
  });

  it("workbench uploads carry the options the same way", async () => {
    await apiMethods.uploadWorkbenchAttachment(new File(["x"], "a.png"), 2, 9, { kind: "qc_report" });
    expect(sent[0].url).toBe("/api/experiments/workbench/2/rows/9/attachments?kind=qc_report");
  });
});
