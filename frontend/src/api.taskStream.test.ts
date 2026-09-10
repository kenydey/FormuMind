/**
 * Task-stream client resilience.
 *
 * The regression these pin: a dropped SSE stream used to be reported as a dead
 * task — or worse, as an unreachable backend — even though the task keeps
 * running server-side and the API is perfectly healthy. A dev-server reload or
 * a backend restart window must be absorbed by reconnect-with-backoff, and the
 * polling fallback is the last resort rather than the first reflex.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { awaitTaskStream, probeBackend } from "./api";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  closed = false;

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  emit(ev: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(ev) } as MessageEvent);
  }

  drop() {
    this.onerror?.(new Event("error"));
  }

  static latest(): FakeEventSource {
    const es = FakeEventSource.instances[FakeEventSource.instances.length - 1];
    if (!es) throw new Error("no EventSource was opened");
    return es;
  }

  static reset() {
    FakeEventSource.instances = [];
  }
}

const jsonResponse = (body: unknown, ok = true) =>
  ({ ok, status: ok ? 200 : 500, json: async () => body }) as unknown as Response;

const COMPLETED_STATUS = {
  task_id: "t1",
  kind: "search",
  state: "completed",
  message: "done",
  progress: 1,
  result: { total: 3 },
};

/** Poll a predicate on real timers — vitest's waitFor is not used here so the
 * same helper works under fake timers. */
async function until(pred: () => boolean, ms = 5_000): Promise<void> {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error("timed out waiting for condition");
    await new Promise((r) => setTimeout(r, 10));
  }
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/health")) return jsonResponse({ status: "ok" });
    if (url.startsWith("/api/tasks/")) return jsonResponse(COMPLETED_STATUS);
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const healthCalls = () =>
  fetchMock.mock.calls.filter(([u]) => String(u).startsWith("/health")).length;

describe("probeBackend", () => {
  it("仅当 /health 真的应答 2xx 才算后端在线", async () => {
    expect(await probeBackend()).toBe(true);

    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    expect(await probeBackend()).toBe(false);

    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    expect(await probeBackend()).toBe(false);
  });
});

describe("awaitTaskStream 断连恢复", () => {
  it("SSE 断连但后端在线：重开流并正常完成，不判死", async () => {
    const settle = awaitTaskStream("t1", undefined, 0, undefined, 0);
    expect(FakeEventSource.instances).toHaveLength(1);

    FakeEventSource.latest().drop();
    // 1 s 退避 → 探活 → 重开
    await until(() => FakeEventSource.instances.length === 2);
    expect(healthCalls()).toBeGreaterThanOrEqual(1);

    FakeEventSource.latest().emit({
      status: "COMPLETED",
      message: "done",
      data: { total: 3 },
    });

    await expect(settle).resolves.toMatchObject({ status: "COMPLETED" });
  });

  it("后端不可达时持续退避，耗尽 5 次预算后才转轮询（2 s 间隔）", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/health")) throw new TypeError("Failed to fetch");
      if (url.startsWith("/api/tasks/")) return jsonResponse(COMPLETED_STATUS);
      return jsonResponse({});
    });

    const settle = awaitTaskStream("t1", undefined, 0, undefined, 0);
    FakeEventSource.latest().drop();

    // 退避序列 1+2+4+8+8 = 23 s，再留出轮询的时间
    await vi.advanceTimersByTimeAsync(30_000);

    await expect(settle).resolves.toMatchObject({ status: "COMPLETED" });
    // 每次都探活一次才决定是否重连 → 预算 5 次全部用过
    expect(healthCalls()).toBeGreaterThanOrEqual(5);
    // 预算耗尽前不轮询：只有一个初始 EventSource，没有第二次重开
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("终态事件照常结束等待（重连逻辑不影响正常路径）", async () => {
    const settle = awaitTaskStream("t1", undefined, 0, undefined, 0);
    FakeEventSource.latest().emit({
      status: "COMPLETED",
      message: "done",
      data: { total: 7 },
    });

    await expect(settle).resolves.toMatchObject({ status: "COMPLETED" });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(healthCalls()).toBe(0);
  });
});
