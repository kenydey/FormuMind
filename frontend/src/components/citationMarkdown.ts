/**
 * Helpers for wiring CitationRenderer to chat / deep-research markdown that
 * already carries backend `[^n]` markers (see citation_binder).
 */
import type { Evidence } from "../api";
import type { CitationAnchor } from "./CitationRenderer";

const CITATION_MARKER_RE = /\[\^\d+\]/;
const FOOTNOTE_START_RE = /^\[\^\d+\]:\s*/m;

/** True when the markdown body contains at least one `[^n]` marker. */
export function hasCitationMarkers(content: string): boolean {
  return CITATION_MARKER_RE.test(content);
}

/**
 * Split a markdown blob into answer body + footnotes section.
 * Returns null when there are no citation markers (caller should use MarkdownMessage).
 */
export function splitCitationMarkdown(
  content: string,
): { answer: string; footnotes: string } | null {
  if (!hasCitationMarkers(content)) return null;
  const fnStart = content.search(FOOTNOTE_START_RE);
  if (fnStart >= 0) {
    return {
      answer: content.slice(0, fnStart).trimEnd(),
      footnotes: content.slice(fnStart).trim(),
    };
  }
  return { answer: content, footnotes: "" };
}

/** Map Evidence[] (1-indexed) onto CitationRenderer anchors. */
export function evidenceToCitationAnchors(citations?: Evidence[]): CitationAnchor[] {
  if (!citations?.length) return [];
  return citations.map((c, i) => {
    const id = String(i + 1);
    const urlCandidate = c.identifier?.startsWith("http")
      ? c.identifier
      : undefined;
    return {
      id,
      title: c.title || c.identifier || c.source || `引用 ${id}`,
      snippet: c.snippet || "",
      url: urlCandidate,
    };
  });
}
