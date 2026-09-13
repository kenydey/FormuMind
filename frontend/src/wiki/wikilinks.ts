/** [[wikilink]] resolve + rewrite for S1 (existing pages only; dead = gray). */

export type WikiLinkIndexEntry = {
  path: string;
  title: string;
  norm_key?: string;
  kind?: string;
};

export type WikiLinkIndex = {
  byPath: Map<string, WikiLinkIndexEntry>;
  byTitle: Map<string, WikiLinkIndexEntry>;
  byNorm: Map<string, WikiLinkIndexEntry>;
};

const WIKILINK_RE = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;

export function buildWikiLinkIndex(pages: WikiLinkIndexEntry[]): WikiLinkIndex {
  const byPath = new Map<string, WikiLinkIndexEntry>();
  const byTitle = new Map<string, WikiLinkIndexEntry>();
  const byNorm = new Map<string, WikiLinkIndexEntry>();
  for (const p of pages) {
    if (p.path) byPath.set(p.path.toLowerCase(), p);
    const stem = p.path.replace(/\.md$/i, "").toLowerCase();
    if (stem) byPath.set(stem, p);
    if (p.title) byTitle.set(p.title.trim().toLowerCase(), p);
    if (p.norm_key) byNorm.set(p.norm_key.trim().toLowerCase(), p);
  }
  return { byPath, byTitle, byNorm };
}

export function resolveWikiLink(
  target: string,
  index: WikiLinkIndex,
): WikiLinkIndexEntry | null {
  const t = (target || "").trim();
  if (!t) return null;
  const lower = t.toLowerCase();
  return (
    index.byPath.get(lower) ||
    index.byPath.get(lower.replace(/\.md$/i, "")) ||
    index.byTitle.get(lower) ||
    index.byNorm.get(lower) ||
    null
  );
}

export function rewriteWikiLinks(markdown: string, index: WikiLinkIndex): string {
  return (markdown || "").replace(WIKILINK_RE, (_m, target: string, label?: string) => {
    const rawTarget = String(target || "").trim();
    const display = (label || rawTarget).trim() || rawTarget;
    const hit = resolveWikiLink(rawTarget, index);
    if (hit) {
      return `[${display}](wiki-path:${encodeURIComponent(hit.path)})`;
    }
    return `[${display}](wiki-dead:${encodeURIComponent(rawTarget)})`;
  });
}

export function parseWikiHref(
  href: string | undefined,
): { kind: "path" | "dead"; value: string } | null {
  if (!href) return null;
  if (href.startsWith("wiki-path:")) {
    return { kind: "path", value: decodeURIComponent(href.slice("wiki-path:".length)) };
  }
  if (href.startsWith("wiki-dead:")) {
    return { kind: "dead", value: decodeURIComponent(href.slice("wiki-dead:".length)) };
  }
  return null;
}
