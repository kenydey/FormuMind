import { useState } from "react";
import "./CitationRenderer.css";

export interface CitationAnchor {
  id: string;
  title: string;
  snippet: string;
  url?: string;
}

interface CitationRendererProps {
  answer: string;
  footnotes: string;
  anchors: CitationAnchor[];
}

interface TextPart {
  type: "text";
  value: string;
}

interface CitationPart {
  type: "citation";
  refId: string;
}

type Part = TextPart | CitationPart;

interface FootnoteEntry {
  id: string;
  content: string;
  anchor?: CitationAnchor;
}

/** Split answer text on [^n] markers, returning interleaved text and citation parts. */
function parseAnswer(answer: string): Part[] {
  const parts: Part[] = [];
  const re = /\[\^(\d+)\]/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = re.exec(answer)) !== null) {
    const before = answer.slice(lastIndex, match.index);
    if (before.length > 0) {
      parts.push({ type: "text", value: before });
    }
    parts.push({ type: "citation", refId: match[1] });
    lastIndex = re.lastIndex;
  }

  const remainder = answer.slice(lastIndex);
  if (remainder.length > 0) {
    parts.push({ type: "text", value: remainder });
  }

  return parts;
}

/** Parse footnotes markdown text into individual footnote entries.
 *  Expected format: lines like `[^1]: some content` possibly spanning multiple lines. */
function parseFootnotes(
  footnotes: string,
  anchors: CitationAnchor[],
): FootnoteEntry[] {
  const entries: FootnoteEntry[] = [];
  const lines = footnotes.split("\n");
  let currentId: string | null = null;
  let currentContent: string[] = [];

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const match = /^\[\^(\d+)\]:\s*(.*)$/.exec(line);
    if (match) {
      // Flush previous entry
      if (currentId !== null) {
        entries.push({
          id: currentId,
          content: currentContent.join("\n").trim(),
          anchor: anchors.find((a) => a.id === currentId),
        });
      }
      currentId = match[1];
      currentContent = match[2] ? [match[2]] : [];
    } else if (currentId !== null && line.trim()) {
      // Continuation line of current footnote
      currentContent.push(line);
    }
  }

  // Flush last entry
  if (currentId !== null) {
    entries.push({
      id: currentId,
      content: currentContent.join("\n").trim(),
      anchor: anchors.find((a) => a.id === currentId),
    });
  }

  return entries;
}

export default function CitationRenderer({
  answer,
  footnotes,
  anchors,
}: CitationRendererProps) {
  const [highlightedId, setHighlightedId] = useState<string | null>(null);

  const answerParts = parseAnswer(answer);
  const footnoteEntries = parseFootnotes(footnotes, anchors);

  function scrollToFootnote(n: string) {
    const el = document.getElementById(`fn-${n}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("citation-flash");
      setTimeout(() => el.classList.remove("citation-flash"), 2000);
    }
  }

  return (
    <div className="citation-renderer">
      {/* Answer text with superscript citation links */}
      <div className="citation-answer leading-relaxed">
        {answerParts.map((part, i) => {
          if (part.type === "text") {
            return <span key={i}>{part.value}</span>;
          }
          return (
            <sup key={i} className="citation-sup">
              <a
                href={`#fn-${part.refId}`}
                onClick={(e) => {
                  e.preventDefault();
                  scrollToFootnote(part.refId);
                }}
                className="citation-link"
                aria-label={`Jump to footnote ${part.refId}`}
              >
                [^{part.refId}]
              </a>
            </sup>
          );
        })}
      </div>

      {/* Footnotes section */}
      {footnoteEntries.length > 0 && (
        <div className="citation-footnotes mt-4">
          <hr className="citation-divider border-edge/40 my-3" />
          <ol className="citation-footnote-list list-decimal pl-5 space-y-3 text-sm text-slate-300">
            {footnoteEntries.map((entry) => (
              <li
                key={entry.id}
                id={`fn-${entry.id}`}
                className={`citation-footnote ${
                  highlightedId === entry.id
                    ? "citation-footnote-highlight bg-accent/10 rounded"
                    : ""
                }`}
                onMouseEnter={() => setHighlightedId(entry.id)}
                onMouseLeave={() => setHighlightedId(null)}
              >
                {entry.anchor && (
                  <div className="citation-anchor-card bg-ink/60 border border-edge/50 rounded p-2 mb-1 text-xs">
                    <div className="font-semibold text-slate-200">
                      {entry.anchor.title}
                    </div>
                    <div className="text-slate-400 mt-0.5">
                      {entry.anchor.snippet}
                    </div>
                    {entry.anchor.url && (
                      <a
                        href={entry.anchor.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="citation-anchor-url text-accent hover:underline break-all"
                      >
                        {entry.anchor.url}
                      </a>
                    )}
                  </div>
                )}
                <span className="citation-footnote-text">{entry.content}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
