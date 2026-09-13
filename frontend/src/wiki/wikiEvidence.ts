import type { Evidence } from "../api";

export function isWikiEvidence(ev: Pick<Evidence, "source" | "identifier">): boolean {
  return ev.source === "wiki" || (ev.identifier || "").startsWith("wiki:");
}

/** `wiki:materials/e51.md` → `materials/e51.md` */
export function wikiPathFromIdentifier(identifier: string): string | null {
  const id = identifier || "";
  if (!id.startsWith("wiki:")) return null;
  return id.slice("wiki:".length) || null;
}

export function wikiTitleFromEvidence(ev: Evidence): string {
  const t = (ev.title || "").trim();
  const m = t.match(/^\[Wiki\/[^\]]+\]\s*(.+)$/i);
  return (m?.[1] || t || wikiPathFromIdentifier(ev.identifier || "") || "Wiki").trim();
}

export function relatedWikiFromCitations(citations?: Evidence[]): {
  path: string;
  title: string;
  kind: string;
}[] {
  if (!citations?.length) return [];
  const out: { path: string; title: string; kind: string }[] = [];
  const seen = new Set<string>();
  for (const c of citations) {
    if (!isWikiEvidence(c)) continue;
    const path = wikiPathFromIdentifier(c.identifier || "");
    if (!path || seen.has(path)) continue;
    seen.add(path);
    const kindMatch = (c.title || "").match(/^\[Wiki\/([^\]]+)\]/i);
    out.push({
      path,
      title: wikiTitleFromEvidence(c),
      kind: kindMatch?.[1] || "page",
    });
  }
  return out;
}
