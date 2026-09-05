/**
 * Pure transforms in the API client.
 *
 * api.ts is the largest file in the frontend and had no tests. These are the
 * functions that sit between the backend and the store, so a silent change in
 * one of them shows up as wrong data on screen rather than as an error.
 */
import { describe, expect, it } from "vitest";
import {
  BACKEND_UNREACHABLE_MESSAGE,
  formatApiError,
  isBackendUnreachableError,
  parseKbIngestData,
  parseSearchStreamData,
  primaryObjectiveMetric,
  progressToTaskStatus,
  sanitizeEvidenceForApi,
} from "./api";
import type { Evidence, Requirement } from "./api";

const evidence = (over: Partial<Evidence> = {}): Evidence =>
  ({
    source: "Patent",
    identifier: "US123",
    title: "A title",
    snippet: "some text",
    relevance: 0.5,
    ...over,
  }) as Evidence;

describe("sanitizeEvidenceForApi", () => {
  it("clamps relevance into [0,1]", () => {
    expect(sanitizeEvidenceForApi(evidence({ relevance: 5 })).relevance).toBe(1);
    expect(sanitizeEvidenceForApi(evidence({ relevance: -2 })).relevance).toBe(0);
    expect(sanitizeEvidenceForApi(evidence({ relevance: 0.42 })).relevance).toBe(0.42);
  });

  it("falls back to 0.5 when relevance is not a number", () => {
    // The backend rejects NaN, so an unparseable value must not be forwarded.
    expect(sanitizeEvidenceForApi(evidence({ relevance: NaN })).relevance).toBe(0.5);
    expect(
      sanitizeEvidenceForApi(evidence({ relevance: "abc" as unknown as number })).relevance
    ).toBe(0.5);
  });

  it("backfills identifier and title from each other", () => {
    expect(sanitizeEvidenceForApi(evidence({ identifier: "", title: "T" })).identifier).toBe("T");
    expect(sanitizeEvidenceForApi(evidence({ identifier: "ID", title: "" })).title).toBe("ID");
  });

  it("never emits an empty required field", () => {
    const out = sanitizeEvidenceForApi(
      evidence({ source: "  ", identifier: "  ", title: "  ", snippet: "  " })
    );
    expect(out.source).toBe("local");
    expect(out.identifier).toBe("source");
    expect(out.title).toBe("source");
    expect(out.snippet).toBe("source");
  });

  it("leaves a well-formed record alone", () => {
    const input = evidence();
    expect(sanitizeEvidenceForApi(input)).toMatchObject({
      source: "Patent",
      identifier: "US123",
      title: "A title",
      snippet: "some text",
      relevance: 0.5,
    });
  });
});

describe("parseSearchStreamData", () => {
  it("returns an empty shape for null", () => {
    expect(parseSearchStreamData(null)).toEqual({
      evidence: [],
      progress: {},
      usedSeedFallback: false,
      filterReport: null,
    });
  });

  it("infers the seed fallback from the evidence when the flag is absent", () => {
    // A seed-corpus result means the live sources returned nothing; the UI
    // warns on it, so inferring it matters when the flag is missing.
    const out = parseSearchStreamData({
      evidence: [evidence({ is_seed_corpus: true } as Partial<Evidence>)],
    });
    expect(out.usedSeedFallback).toBe(true);
  });

  it("honours an explicit fallback flag", () => {
    expect(parseSearchStreamData({ evidence: [], used_seed_fallback: true }).usedSeedFallback).toBe(
      true
    );
  });

  it("defaults total to the evidence length", () => {
    const out = parseSearchStreamData({ evidence: [evidence(), evidence()] });
    expect(out.progress.total).toBe(2);
  });

  it("ignores a malformed filter report rather than passing it through", () => {
    expect(parseSearchStreamData({ filter_report: [] }).filterReport).toBeNull();
    expect(parseSearchStreamData({ filter_report: "nope" }).filterReport).toBeNull();
    expect(parseSearchStreamData({ filter_report: { kept: 3 } }).filterReport).toEqual({ kept: 3 });
  });

  it("coerces non-array source lists to arrays", () => {
    const out = parseSearchStreamData({ sources_done: "patents", sources_pending: null });
    expect(out.progress.sourcesDone).toEqual([]);
    expect(out.progress.sourcesPending).toEqual([]);
  });
});

describe("formatApiError", () => {
  it("uses the message of an Error", () => {
    expect(formatApiError(new Error("boom"))).toBe("boom");
  });

  it("stringifies anything else", () => {
    expect(formatApiError("plain")).toBe("plain");
    expect(formatApiError(42)).toBe("42");
    expect(formatApiError(null)).toBe("null");
  });

  it("normalises browser/proxy unreachable errors to Chinese copy", () => {
    expect(formatApiError(new Error("Load failed"))).toBe(BACKEND_UNREACHABLE_MESSAGE);
    expect(formatApiError(new Error("Failed to fetch"))).toBe(BACKEND_UNREACHABLE_MESSAGE);
    expect(formatApiError(new Error("/api/projects -> 500"))).toBe(BACKEND_UNREACHABLE_MESSAGE);
  });
});

describe("isBackendUnreachableError", () => {
  it("detects WebKit Load failed and Vite proxy 5xx", () => {
    expect(isBackendUnreachableError("Load failed")).toBe(true);
    expect(isBackendUnreachableError("/api/projects -> 500")).toBe(true);
    expect(isBackendUnreachableError(BACKEND_UNREACHABLE_MESSAGE)).toBe(true);
    expect(isBackendUnreachableError("validation failed")).toBe(false);
  });
});

describe("primaryObjectiveMetric", () => {
  it("prefers the first declared objective", () => {
    const req = {
      domain: "anticorrosion_coating",
      objectives: [{ metric: "cost_cny_per_kg" }, { metric: "salt_spray_hours" }],
    } as unknown as Requirement;
    expect(primaryObjectiveMetric(req)).toBe("cost_cny_per_kg");
  });

  it("falls back to the domain default when none are declared", () => {
    const req = { domain: "degreaser", objectives: [] } as unknown as Requirement;
    expect(primaryObjectiveMetric(req)).toBe("cleaning_efficiency");
  });
});

describe("progressToTaskStatus", () => {
  const ev = (over: Record<string, unknown> = {}) =>
    ({ status: "RUNNING", progress: 0.4, message: "working", ...over }) as never;

  it("carries the terminal result through", () => {
    const status = progressToTaskStatus("t1", "optimize", ev({ status: "COMPLETED", progress: 1, data: { ok: true } }));
    expect(status.state).toBe("completed");
    expect(status.task_id).toBe("t1");
    expect(status.result).toEqual({ ok: true });
  });

  it("maps a failure to the failed state", () => {
    expect(progressToTaskStatus("t2", "optimize", ev({ status: "FAILED" })).state).toBe("failed");
  });

  it("defaults a missing progress to zero rather than undefined", () => {
    const status = progressToTaskStatus("t3", "optimize", ev({ progress: undefined }));
    expect(status.progress).toBe(0);
    expect(status.result).toBeNull();
  });

  it("builds the stream url from the task id", () => {
    expect(progressToTaskStatus("abc", "search", ev()).stream_url).toBe("/api/tasks/abc/stream");
  });
});

describe("parseKbIngestData", () => {
  it("returns null without a docs array", () => {
    expect(parseKbIngestData(null)).toBeNull();
    expect(parseKbIngestData({ indexed: 3 })).toBeNull();
  });

  it("prefers explicit counters", () => {
    const out = parseKbIngestData({ docs: [], indexed: 3, failed: 1, done: 4, total: 9 });
    expect(out).toMatchObject({ indexed: 3, failed: 1, done: 4, total: 9 });
  });

  it("derives counters from the docs when absent", () => {
    // The backend omits the totals on early events, so the UI would show
    // zero progress mid-ingest if these were not derived.
    const docs = [{ status: "indexed" }, { status: "indexed" }, { status: "failed" }];
    const out = parseKbIngestData({ docs });
    expect(out).toMatchObject({ indexed: 2, failed: 1, total: 3 });
  });
});
