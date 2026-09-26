// Standalone helpers formerly below export const api (P2).
import {
  apiAuthHeaders,
  getApiToken
} from "./http";
import type {
  Evidence,
  FilterReport,
  KbIngestDoc,
  KbIngestProgress,
  SearchStreamProgress,
  TaskProgressEvent,
  TaskProgressStatus,
  TaskStatus
} from "./types";
import { apiMethods as api } from "./methods";


const TASK_STATE_MAP: Record<TaskProgressStatus, TaskStatus["state"]> = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELLED: "cancelled",
};

/** Map SSE progress event to legacy TaskStatus snapshot shape. */
export function progressToTaskStatus(
  taskId: string,
  kind: string,
  ev: TaskProgressEvent
): TaskStatus {
  return {
    task_id: taskId,
    kind,
    state: TASK_STATE_MAP[ev.status],
    progress: ev.progress ?? 0,
    message: ev.message,
    result: ev.data ?? null,
    stream_url: `/api/tasks/${taskId}/stream`,
    stage: (ev as any).stage ?? "",
    elapsed_ms: (ev as any).elapsed_ms ?? null,
  };
}

function streamUrl(path: string): string {
  const token = getApiToken();
  if (!token) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}token=${encodeURIComponent(token)}`;
}

/**
 * Is the API process actually accepting traffic?
 *
 * Used to separate "this stream/task broke" from "the backend is down" — the
 * two need different copy and different recovery. Deliberately short: during a
 * backend restart the probe has to answer quickly enough to be worth asking
 * (1–8 s reconnect backoff budgets depend on it). Any failure (abort, network
 * error, non-2xx) means "not ready".
 */
export async function probeBackend(timeoutMs = 2_000): Promise<boolean> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch("/health", {
      headers: apiAuthHeaders(),
      signal: ctrl.signal,
    });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(t);
  }
}

/** Subscribe to task SSE progress (GET /api/tasks/{id}/stream). */
export function subscribeTaskStream(
  taskId: string,
  onEvent: (ev: TaskProgressEvent) => void,
  onError?: (err: Event) => void
): EventSource {
  const es = new EventSource(streamUrl(`/api/tasks/${taskId}/stream`));
  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data) as TaskProgressEvent);
    } catch {
      // ignore malformed frames
    }
  };
  es.onerror = onError ?? (() => es.close());
  return es;
}

/** Await task completion via EventSource; resolves with terminal COMPLETED event. */
/**
 * Wait for a background task, streaming its progress.
 *
 * `timeoutMs = 0` disables the wall-clock limit, for jobs whose duration is
 * genuinely unbounded — building a knowledge base from several hundred documents
 * takes as long as the downloads take, and cutting it off at a fixed number is
 * arbitrary. Note that this timeout only ever stopped the *client* watching; the
 * server-side task carries on regardless, which is why the old copy claiming the
 * build had been "interrupted" was wrong.
 *
 * `idleTimeoutMs` is what makes an unlimited wait safe. It resets on every
 * progress event, so a slow job never trips it, but a worker that has died — OOM
 * killed, container restarted — stops emitting and is reported instead of
 * spinning forever. Total duration unlimited, silence bounded.
 *
 * A dropped stream is not a failed job: the task keeps running server-side, so
 * the stream is reopened with backoff (≤5 attempts, 1→8 s, each gated on a
 * `/health` probe) before long-polling takes over at 2 s. This keeps a
 * dev-server reload or a backend restart window from being reported as a dead
 * task — or worse, as an unreachable backend.
 */
export function awaitTaskStream(
  taskId: string,
  onEvent?: (ev: TaskProgressEvent) => void,
  timeoutMs = 120_000,
  signal?: AbortSignal,
  idleTimeoutMs = 0
): Promise<TaskProgressEvent> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let es: EventSource | undefined;
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    /** How many times this wait has already reconnected after a dropped SSE. */
    let reconnects = 0;

    /** A dropped stream is normally transient (dev-server reload, a backend
     * restart window) and the task survives it server-side, so reconnect with
     * backoff first; the polling fallback is the last resort, not the reflex. */
    const MAX_SSE_RECONNECTS = 5;
    const RECONNECT_BASE_MS = 1_000;
    const RECONNECT_MAX_MS = 8_000;
    /** Polling cadence once SSE is abandoned. A multi-hour ingest does not
     * need sub-second updates, and 400 ms polling is pure noise. */
    const FALLBACK_POLL_MS = 2_000;

    const clearIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = null;
    };

    const armIdle = () => {
      if (idleTimeoutMs <= 0) return;
      clearIdle();
      idleTimer = setTimeout(() => {
        es?.close();
        finish(() =>
          reject(
            new Error(
              `已 ${Math.round(idleTimeoutMs / 1000)}s 没有进度更新 — 任务可能已中止（请查看服务端日志）`
            )
          )
        );
      }, idleTimeoutMs);
    };

    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      clearIdle();
      signal?.removeEventListener("abort", onAbort);
      fn();
    };

    const onAbort = () => {
      es?.close();
      finish(() => reject(new Error("任务已取消")));
    };

    const resolveFromStatus = (s: TaskStatus) => {
      const map: Record<string, TaskProgressStatus> = { completed: "COMPLETED", failed: "FAILED", cancelled: "CANCELLED", pending: "PENDING", running: "RUNNING" };
      const ev: TaskProgressEvent = {
        status: (map[s.state] as TaskProgressStatus) || "FAILED",
        message: s.message,
        progress: s.progress,
        stage: s.stage || "",
        data: s.result ?? undefined,
      };
      onEvent?.(ev);
      if (s.state === "completed") {
        finish(() => resolve(ev));
      } else if (s.state === "cancelled") {
        finish(() => reject(new Error(s.message || "任务已取消")));
      } else {
        finish(() => reject(new Error(s.message || "任务失败")));
      }
    };

    const timer =
      timeoutMs > 0
        ? setTimeout(() => {
            es?.close();
            finish(() => reject(new Error(`任务超时（${Math.round(timeoutMs / 1000)}s）`)));
          }, timeoutMs)
        : null;

    const handleEvent = (ev: TaskProgressEvent) => {
      armIdle();  // progress means alive — restart the silence clock
      onEvent?.(ev);
      if (ev.status === "COMPLETED" || ev.status === "FAILED" || ev.status === "CANCELLED") {
        es?.close();
        if (ev.status === "FAILED" || ev.status === "CANCELLED") {
          finish(() => reject(new Error(ev.message || (ev.status === "CANCELLED" ? "任务已取消" : "任务失败"))));
        } else {
          finish(() => resolve(ev));
        }
      }
    };

    /**
     * Long-poll the task endpoint — only reached once the reconnect budget is
     * spent. An unlimited wall clock has to mean the fallback is unlimited too,
     * otherwise a dropped SSE connection reintroduces a 120 s ceiling by the
     * back door — which is exactly how a long job "times out" while healthy.
     */
    const startPollFallback = () => {
      pollTask(
        taskId,
        (s) => {
          if (settled) return;  // 已 settle：停止向 onEvent 泄漏事件
          armIdle();
          if (s.state === "running" || s.state === "pending") {
            onEvent?.({
              status: s.state === "running" ? "RUNNING" : "PENDING",
              message: s.message,
              progress: s.progress,
            });
          }
        },
        FALLBACK_POLL_MS,
        timeoutMs > 0 ? undefined : 0,
      )
        .then((s) => {
          if (settled) return;  // 超时/取消已先 settle：丢弃迟到的轮询结果
          resolveFromStatus(s);
        })
        .catch(() => {
          finish(() =>
            reject(
              new Error(
                "SSE 连接中断 — 无法获取任务进度（请检查后端服务；若未启动 Redis，请确认后端已升级支持无 Redis 降级）"
              )
            )
          );
        });
    };

    /**
     * The SSE stream dropped. The task itself is unaffected — its state lives
     * server-side in Redis/disk — so treat this as a transport problem and
     * reconnect with backoff before falling back. A dev-server reload or a
     * backend restart window recovers on the first or second attempt. The
     * budget is never refunded, so repeated flapping still converges on the
     * deterministic polling fallback instead of reconnecting forever.
     */
    const handleDrop = () => {
      es?.close();
      if (settled) return;
      void (async () => {
        while (reconnects < MAX_SSE_RECONNECTS && !settled) {
          const delay = Math.min(
            RECONNECT_BASE_MS * 2 ** reconnects,
            RECONNECT_MAX_MS
          );
          reconnects += 1;
          await new Promise((r) => setTimeout(r, delay));
          if (settled) return;
          // Reconnecting into a backend that is not accepting traffic just
          // burns the budget, so keep backing off while the probe says no.
          if (!(await probeBackend())) continue;
          if (settled) return;
          openStream();
          return;
        }
        if (settled) return;
        startPollFallback();
      })();
    };

    const openStream = () => {
      if (settled) return;
      es = subscribeTaskStream(taskId, handleEvent, handleDrop);
    };

    signal?.addEventListener("abort", onAbort, { once: true });
    armIdle();
    openStream();
  });
}

// ── v0.3 新增类型 ────────────────────────────────────────────────────────────

export function parseSearchStreamData(
  data: Record<string, unknown> | null | undefined
): {
  evidence: Evidence[];
  progress: Partial<SearchStreamProgress>;
  usedSeedFallback: boolean;
  filterReport: FilterReport | null;
} {
  if (!data) {
    return { evidence: [], progress: {}, usedSeedFallback: false, filterReport: null };
  }
  const evidence = Array.isArray(data.evidence) ? (data.evidence as Evidence[]) : [];
  const usedSeedFallback =
    data.used_seed_fallback === true || evidence.some((e) => e.is_seed_corpus);
  const rawReport = data.filter_report;
  const filterReport =
    rawReport && typeof rawReport === "object" && !Array.isArray(rawReport)
      ? (rawReport as FilterReport)
      : null;
  return {
    evidence,
    usedSeedFallback,
    filterReport,
    progress: {
      total: typeof data.total === "number" ? data.total : evidence.length,
      source: typeof data.source === "string" ? data.source : null,
      newCount: typeof data.new_count === "number" ? data.new_count : 0,
      sourcesDone: Array.isArray(data.sources_done) ? (data.sources_done as string[]) : [],
      sourcesPending: Array.isArray(data.sources_pending) ? (data.sources_pending as string[]) : [],
    },
  };
}

/** Per-document status of the background KB ingest task (SSE data.docs). */
export function parseKbIngestData(
  data: Record<string, unknown> | null | undefined
): KbIngestProgress | null {
  if (!data || !Array.isArray(data.docs)) return null;
  const docs = data.docs as KbIngestDoc[];
  return {
    docs,
    done: typeof data.done === "number" ? data.done : 0,
    total: typeof data.total === "number" ? data.total : docs.length,
    indexed:
      typeof data.indexed === "number"
        ? data.indexed
        : docs.filter((d) => d.status === "indexed").length,
    failed:
      typeof data.failed === "number"
        ? data.failed
        : docs.filter((d) => d.status === "failed").length,
  };
}

export async function pollTask(
  id: string,
  onUpdate: (s: TaskStatus) => void,
  intervalMs = 400,
  /** 0 = poll until the task finishes, for jobs with no meaningful deadline. */
  maxAttempts = 300
): Promise<TaskStatus> {
  let consecutiveFailures = 0;
  for (let attempt = 0; !maxAttempts || attempt < maxAttempts; attempt++) {
    let s: TaskStatus;
    try {
      s = await api.task(id);
      consecutiveFailures = 0;
    } catch (e) {
      consecutiveFailures += 1;
      // A transient failure (dev-server reload, a network blip) must not kill
      // the progress tracking of a multi-hour ingest. The task state lives in
      // Redis/disk and is still there once the backend comes back, so retry —
      // give up only after repeated consecutive failures.
      if (consecutiveFailures >= 5) throw e;
      await new Promise((r) => setTimeout(r, intervalMs * 5));
      continue;
    }
    onUpdate(s);
    if (s.state === "completed" || s.state === "failed") return s;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`任务轮询超时（${maxAttempts} 次）`);
}

// ── 2026-09-05 材料库 / 结构搜索 / 会话 / KB-KG 诊断类型(与后端 view 宽松对齐) ──

