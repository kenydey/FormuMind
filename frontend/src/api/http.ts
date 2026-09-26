// HTTP client helpers (P2 split from api.ts).
import type { AsyncTaskAccepted, Evidence } from "./types";

export class ApiError extends Error {
  /** Raw FastAPI `detail` when it was an object (e.g. message + candidates). */
  detail?: unknown;
  /** Slot / suggestion names when a 404 carries structured candidates. */
  candidates?: string[];

  constructor(
    message: string,
    opts?: { detail?: unknown; candidates?: string[] }
  ) {
    super(message);
    this.name = "ApiError";
    this.detail = opts?.detail;
    this.candidates = opts?.candidates;
  }
}

export function formatDetailObject(detail: Record<string, unknown>): {
  message: string;
  candidates?: string[];
} {
  const candidates = Array.isArray(detail.candidates)
    ? detail.candidates.map((c) => String(c)).filter(Boolean)
    : undefined;
  const base =
    typeof detail.message === "string" && detail.message.trim()
      ? detail.message
      : typeof detail.msg === "string"
        ? detail.msg
        : JSON.stringify(detail);
  return { message: base, candidates };
}

export async function readApiError(res: Response, path: string): Promise<ApiError> {
  let message = `${path} -> ${res.status}`;
  let detail: unknown;
  let candidates: string[] | undefined;
  try {
    const body = (await res.json()) as { detail?: unknown };
    detail = body.detail;
    if (typeof body.detail === "string") {
      message = body.detail;
    } else if (Array.isArray(body.detail)) {
      message = body.detail
        .map((item) =>
          typeof item === "object" && item && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : String(item)
        )
        .join("；");
    } else if (body.detail && typeof body.detail === "object") {
      const formatted = formatDetailObject(body.detail as Record<string, unknown>);
      message = formatted.message;
      candidates = formatted.candidates;
    }
  } catch {
    // keep status fallback
  }
  return new ApiError(message, { detail, candidates });
}

/** Stable Chinese copy when the API process / Vite proxy is unreachable. */
export const BACKEND_UNREACHABLE_MESSAGE =
  "后端不可达（无法连接 API）。请确认 uvicorn 已在 :8000 启动。";

/**
 * True for browser/proxy failures that mean "backend not accepting traffic"
 * rather than an application-level 4xx/5xx business error.
 *
 * Do NOT treat every `-> 5xx` app error as unreachable — only empty-body
 * Vite proxy failures (`/api/... -> 500` with no parsed detail) and genuine
 * network failures. Application Internal Server Error must stay visible.
 */
export function isBackendUnreachableError(message: string): boolean {
  const m = message.trim();
  if (!m) return false;
  if (m.includes("后端不可达")) return true;
  const lower = m.toLowerCase();
  if (lower === "load failed") return true;
  if (lower.includes("failed to fetch")) return true;
  if (lower.includes("networkerror")) return true;
  if (lower.includes("network request failed")) return true;
  if (lower.includes("econnrefused")) return true;
  // Vite proxy when uvicorn is down: path-only status fallback, empty body.
  // e.g. "/api/projects -> 500" — not "Internal Server Error" from FastAPI.
  if (/^\/api\/\S+\s*->\s*5\d\d\b/.test(m)) return true;
  if (/^\/health\s*->\s*5\d\d\b/.test(m)) return true;
  return false;
}

export function formatApiError(err: unknown): string {
  const raw =
    err instanceof ApiError
      ? err.message
      : err instanceof Error
        ? err.message
        : String(err);
  if (isBackendUnreachableError(raw)) return BACKEND_UNREACHABLE_MESSAGE;
  if (
    err instanceof ApiError &&
    Array.isArray(err.candidates) &&
    err.candidates.length > 0
  ) {
    return `${raw}（可选：${err.candidates.join("、")}）`;
  }
  return raw;
}

/** Normalize evidence before POST /api/chat (clamp relevance, fill required fields). */
export function sanitizeEvidenceForApi(ev: Evidence): Evidence {
  const rel = Number(ev.relevance);
  const identifier = (ev.identifier || ev.title || "source").trim() || "source";
  const title = (ev.title || identifier).trim() || identifier;
  const snippet = (ev.snippet ?? "").trim();
  return {
    ...ev,
    source: (ev.source || "local").trim() || "local",
    identifier,
    title,
    snippet: snippet || title,
    relevance: Number.isFinite(rel) ? Math.min(1, Math.max(0, rel)) : 0.5,
  };
}

const API_TOKEN_STORAGE_KEY = "formumind-api-token";

export function getApiToken(): string | null {
  // Runtime-only: never bake FORMUMIND_API_TOKEN into the Vite bundle
  // (build-arg VITE_API_TOKEN was removed — anyone who can download static
  // assets could read a baked secret). Enter the token in Settings.
  try {
    const stored = localStorage.getItem(API_TOKEN_STORAGE_KEY);
    return stored?.trim() || null;
  } catch {
    return null;
  }
}

export function setApiToken(token: string): void {
  localStorage.setItem(API_TOKEN_STORAGE_KEY, token.trim());
}

export function apiAuthHeaders(): Record<string, string> {
  const token = getApiToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function jsonHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", ...apiAuthHeaders() };
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await readApiError(res, path);
  return res.json();
}

export async function postAccepted(path: string, body: unknown): Promise<AsyncTaskAccepted> {
  const res = await fetch(path, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  if (res.status !== 202) throw await readApiError(res, path);
  return res.json();
}

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: apiAuthHeaders() });
  if (!res.ok) throw await readApiError(res, path);
  return res.json();
}

export async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await readApiError(res, path);
  return res.json();
}

export async function del<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: "DELETE", headers: apiAuthHeaders() });
  if (!res.ok) throw await readApiError(res, path);
  return res.json();
}

