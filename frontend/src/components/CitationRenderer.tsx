import { useMemo, useState } from "react";
import MarkdownMessage from "./MarkdownMessage";
import "./CitationRenderer.css";

export interface CitationAnchor {
  id: string;
  title: string;
  snippet: string;
  url?: string;
  /** W3-14: 页码定位(后端 Passage page_no / Evidence.page)。 */
  page?: number | null;
  /** W3-14: KB source id —— 有值且传入 onJumpToSource 时页码 badge 可点击跳转。 */
  sourceId?: string | null;
}

interface CitationRendererProps {
  answer: string;
  footnotes: string;
  anchors: CitationAnchor[];
  /** W3-14: 页码 badge 点击跳转(打开 SourceDetail 并滚动到对应 chunk)。 */
  onJumpToSource?: (sourceId: string, page: number | null) => void;
}

interface FootnoteEntry {
  id: string;
  content: string;
  anchor?: CitationAnchor;
}

/**
 * 引用占位符：渲染前把 [^n] 换成 PUA 字符 token。Markdown 解析器不会触碰
 * 这些字符，整段答案可直接复用 MarkdownMessage 的完整渲染管线（GFM 表格 /
 * KaTeX / SMILES），排版与无引用答案完全一致。
 */
const PH_OPEN = "\uE000";
const PH_CLOSE = "\uE001";

function maskCitationMarkers(answer: string): { text: string; ids: string[] } {
  const ids: string[] = [];
  const maskOne = (s: string) =>
    s.replace(/\[\^(\d+)\]/g, (_m, id: string) => {
      ids.push(id);
      return `${PH_OPEN}${ids.length - 1}${PH_CLOSE}`;
    });
  // 围栏代码块与行内代码保持原文 —— 仅对代码之外的文本做占位符替换。
  const text = answer
    .split(/(```[\s\S]*?```)/g)
    .map((chunk, i) =>
      i % 2 === 1
        ? chunk
        : chunk
            .split(/(`[^`\n]*`)/g)
            .map((c, j) => (j % 2 === 1 ? c : maskOne(c)))
            .join(""),
    )
    .join("");
  return { text, ids };
}

/**
 * rehype 插件：把文本节点中的占位符切成 <sup><a>[^n]</a></sup> 上标引用链接。
 * pre/code 子树跳过 —— 代码块内的 [^n] 保持原文。
 *
 * 返回 unified attacher（unified 调用它拿到真正的 transformer）。
 */
function rehypeCitationRefs(ids: string[]): () => (tree: any) => void {
  const tokenRe = new RegExp(`(${PH_OPEN}\\d+${PH_CLOSE})`, "g");
  const singleRe = new RegExp(`^${PH_OPEN}(\\d+)${PH_CLOSE}$`);

  const walk = (node: any): void => {
    if (!node || typeof node !== "object") return;
    if (node.type === "element" && (node.tagName === "pre" || node.tagName === "code")) {
      return;
    }
    const children: any[] | undefined = node.children;
    if (!Array.isArray(children)) return;
    const next: any[] = [];
    for (const child of children) {
      if (child && child.type === "text" && typeof child.value === "string") {
        const parts = child.value.split(tokenRe);
        if (parts.length === 1) {
          next.push(child);
          continue;
        }
        for (const part of parts) {
          const m = singleRe.exec(part);
          if (m) {
            const refId = ids[Number(m[1])];
            next.push({
              type: "element",
              tagName: "sup",
              properties: { className: ["citation-sup"] },
              children: [
                {
                  type: "element",
                  tagName: "a",
                  properties: {
                    href: `#fn-${refId}`,
                    className: ["citation-link"],
                    ariaLabel: `Jump to footnote ${refId}`,
                  },
                  children: [{ type: "text", value: `[^${refId}]` }],
                },
              ],
            });
          } else if (part) {
            next.push({ type: "text", value: part });
          }
        }
      } else {
        walk(child);
        next.push(child);
      }
    }
    node.children = next;
  };

  // unified attacher：被调用后返回真正作用于 tree 的 transformer。
  return () => (tree: any) => walk(tree);
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
  onJumpToSource,
}: CitationRendererProps) {
  const [highlightedId, setHighlightedId] = useState<string | null>(null);

  const { text: maskedAnswer, ids } = useMemo(
    () => maskCitationMarkers(answer),
    [answer],
  );
  const extraRehype = useMemo(() => [rehypeCitationRefs(ids)], [ids]);
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
      {/* Answer text: full markdown via MarkdownMessage, [^n] restored as sup links by the rehype plugin */}
      <div
        className="citation-answer leading-relaxed"
        onClick={(e) => {
          const anchor = (e.target as HTMLElement).closest?.("a.citation-link");
          if (!anchor) return;
          e.preventDefault();
          const refId = (anchor.getAttribute("href") || "").replace(/^#fn-/, "");
          if (refId) scrollToFootnote(refId);
        }}
      >
        <MarkdownMessage content={maskedAnswer} rehypePlugins={extraRehype} />
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
                    <div className="font-semibold text-slate-200 flex items-center gap-1.5">
                      <span className="min-w-0 truncate">{entry.anchor.title}</span>
                      {/* W3-14: 页码 badge */}
                      {entry.anchor.page != null &&
                        (onJumpToSource && entry.anchor.sourceId ? (
                          <button
                            type="button"
                            data-testid={`citation-page-jump-${entry.id}`}
                            onClick={() =>
                              onJumpToSource(entry.anchor!.sourceId!, entry.anchor!.page ?? null)
                            }
                            className="shrink-0 text-[9px] font-mono px-1.5 py-px rounded border border-accent/40 text-accent bg-accent/10 hover:bg-accent/20"
                            title={`跳转到第 ${entry.anchor.page} 页切块`}
                          >
                            p.{entry.anchor.page}
                          </button>
                        ) : (
                          <span
                            className="shrink-0 text-[9px] font-mono px-1.5 py-px rounded border border-edge text-slate-500"
                            data-testid={`citation-page-badge-${entry.id}`}
                            title={`页码 ${entry.anchor.page}`}
                          >
                            p.{entry.anchor.page}
                          </span>
                        ))}
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
                <div className="citation-footnote-text">
                  <MarkdownMessage content={entry.content} />
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
