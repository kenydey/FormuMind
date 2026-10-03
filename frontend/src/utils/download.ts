import { apiAuthHeaders, readApiError } from "../api/http";

/** Filename from a Content-Disposition header (RFC 5987 ``filename*`` or plain). */
export function filenameFromDisposition(header: string | null | undefined): string | null {
  if (!header) return null;
  const star = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
    } catch {
      /* fall through to the plain form */
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : null;
}

/**
 * Download an authenticated API resource.
 *
 * A plain ``<a href="/api/...">`` navigation cannot carry the ``Authorization``
 * header, so with API auth enabled (the default for public deployments) every
 * such link answered 401. This fetches with the bearer token and hands the blob
 * to the browser.
 */
export async function downloadWithAuth(url: string, fallbackName = "download"): Promise<string> {
  const res = await fetch(url, { headers: apiAuthHeaders() });
  if (!res.ok) throw await readApiError(res, url);
  const blob = await res.blob();
  const name = filenameFromDisposition(res.headers.get("Content-Disposition")) || fallbackName;
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = name;
  document.body.appendChild(a);
  try {
    a.click();
  } finally {
    document.body.removeChild(a);
    // Not synchronously: Safari / Firefox may still be reading the blob when click()
    // returns, and an immediate revoke cancels the download (FileSaver.js waits 40 s).
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 40_000);
  }
  return name;
}
