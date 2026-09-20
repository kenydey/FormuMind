import { memo, useMemo, useState } from "react";
import { type Components } from "react-markdown";
import MarkdownMessage from "./MarkdownMessage";
import { parseWikiFrontMatter } from "../wiki/frontMatter";
import {
  buildWikiLinkIndex,
  deadWikiTip,
  parseWikiHref,
  rewriteWikiLinks,
  type WikiLinkIndexEntry,
} from "../wiki/wikilinks";

export type WikiReaderPage = {
  path: string;
  title: string;
  kind?: string;
  flags?: string[];
  source_ids?: string[];
  markdown: string;
  norm_key?: string;
  updated_at?: string | null;
};

type Props = {
  page: WikiReaderPage;
  linkPages?: WikiLinkIndexEntry[];
  onNavigatePath?: (path: string) => void;
  className?: string;
};

function WikiMarkdownReader({ page, linkPages = [], onNavigatePath, className }: Props) {
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const { meta, body } = useMemo(
    () => parseWikiFrontMatter(page.markdown || ""),
    [page.markdown],
  );

  const index = useMemo(() => {
    const self: WikiLinkIndexEntry = {
      path: page.path,
      title: page.title,
      norm_key: page.norm_key,
      kind: page.kind,
    };
    const merged = [...linkPages];
    if (page.path && !merged.some((p) => p.path === page.path)) merged.push(self);
    return buildWikiLinkIndex(merged);
  }, [linkPages, page.path, page.title, page.norm_key, page.kind]);

  const rewritten = useMemo(() => rewriteWikiLinks(body, index), [body, index]);

  const flags = page.flags?.length
    ? page.flags
    : Array.isArray(meta.flags)
      ? (meta.flags as string[])
      : typeof meta.flags === "string" && meta.flags
        ? [String(meta.flags)]
        : [];

  const sourceIds = page.source_ids?.length
    ? page.source_ids
    : Array.isArray(meta.source_ids)
      ? (meta.source_ids as string[])
      : [];

  const boundsHint =
    typeof meta.bounds_json === "string"
      ? meta.bounds_json
      : meta.bounds_json != null
        ? JSON.stringify(meta.bounds_json)
        : null;

  const components: Components = useMemo(
    () => ({
      a({ href, children }) {
        const wiki = parseWikiHref(href);
        if (wiki?.kind === "path") {
          return (
            <button
              type="button"
              className="text-accent underline underline-offset-2 hover:opacity-90"
              title={wiki.value}
              onClick={() => onNavigatePath?.(wiki.value)}
            >
              {children}
            </button>
          );
        }
        if (wiki?.kind === "dead") {
          return (
            <span
              className="text-slate-500 cursor-not-allowed no-underline"
              title={deadWikiTip(wiki.value)}
            >
              {children}
            </span>
          );
        }
        return (
          <a href={href} className="text-accent" target="_blank" rel="noreferrer">
            {children}
          </a>
        );
      },
      img({ src, alt }) {
        if (!src) return null;
        return (
          <button
            type="button"
            className="block my-2 max-w-full text-left"
            title="点击放大"
            onClick={() => setLightboxSrc(src)}
          >
            <img
              src={src}
              alt={alt || ""}
              className="max-w-full max-h-64 rounded border border-edge/50 cursor-zoom-in"
            />
          </button>
        );
      },
    }),
    [onNavigatePath],
  );

  return (
    <div
      className={`flex flex-col gap-2 text-sm text-slate-300 ${className || ""}`}
      data-testid="wiki-markdown-reader"
    >
      <header className="font-sans shrink-0 space-y-1">
        <div className="text-base text-slate-100 font-medium">{page.title || page.path}</div>
        <div className="text-[10px] text-slate-500 flex flex-wrap gap-2 items-center">
          {page.kind && (
            <span className="border border-edge/50 rounded px-1.5 py-0.5">{page.kind}</span>
          )}
          <code className="text-slate-400">{page.path}</code>
          {page.updated_at && <span>更新 {page.updated_at}</span>}
        </div>
        {flags.length > 0 && (
          <div className="flex flex-wrap gap-1" data-testid="wiki-flag-bar">
            {flags.map((f) => (
              <span
                key={f}
                className="text-[10px] text-amber-300 border border-amber-500/40 rounded px-1.5 py-0.5"
              >
                {f}
              </span>
            ))}
          </div>
        )}
        {(sourceIds.length > 0 || boundsHint) && (
          <div
            className="rounded border border-edge/50 bg-ink/40 px-2 py-1.5 text-[11px] space-y-1"
            data-testid="wiki-frontmatter-card"
          >
            {sourceIds.length > 0 && (
              <div className="flex flex-wrap gap-1 items-center">
                <span className="text-slate-500 shrink-0">来源</span>
                {sourceIds.map((sid) => (
                  <span
                    key={sid}
                    className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-panel/40 border border-edge/40 text-slate-300"
                    title={sid}
                  >
                    {sid.length > 18 ? `${sid.slice(0, 16)}…` : sid}
                  </span>
                ))}
              </div>
            )}
            {boundsHint && (
              <div className="text-slate-400 truncate" title={boundsHint}>
                bounds: {boundsHint}
              </div>
            )}
          </div>
        )}
      </header>

      <MarkdownMessage
        content={rewritten || "_empty_"}
        components={components}
        enableMermaid
      />

      {lightboxSrc && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          data-testid="wiki-image-lightbox"
          onClick={() => setLightboxSrc(null)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setLightboxSrc(null);
          }}
        >
          <img
            src={lightboxSrc}
            alt=""
            className="max-w-[95vw] max-h-[90vh] rounded shadow-lg"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}

export default memo(WikiMarkdownReader);
