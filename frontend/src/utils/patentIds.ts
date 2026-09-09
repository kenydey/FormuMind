/** Patent publication-number helpers (mirror backend patent_ids.py). */

const OFFICES = "CN|US|EP|WO|JP|KR|DE|GB|FR";

export function normalizePatentPub(raw: string | null | undefined): string | null {
  if (!raw) return null;
  let s = String(raw).trim().split("?")[0].split("#")[0];
  const upper = s.toUpperCase();
  if (upper.includes("PATENT/")) {
    const parts = s.split("/");
    s = parts[parts.length - 1] || s;
  }
  const compactTry = s.toUpperCase().replace(/[\s\-_/]/g, "");
  const m = compactTry.match(new RegExp(`^(${OFFICES})([A-Z]?)(\\d{4,}[A-Z0-9]*)$`, "i"));
  if (m) {
    return `${m[1].toUpperCase()}${ (m[2] || "").toUpperCase() }${m[3].toUpperCase()}`;
  }
  const m2 = compactTry.match(new RegExp(`(${OFFICES})[A-Z]?\\d{4,}[A-Z0-9]*`, "i"));
  return m2 ? m2[0].toUpperCase() : null;
}

export function hyphenatedScpn(compact: string | null | undefined): string | null {
  if (!compact) return null;
  const m = compact.toUpperCase().match(/^([A-Z]{2})(\d+)([A-Z]\d*)?$/);
  if (!m) return null;
  return m[3] ? `${m[1]}-${m[2]}-${m[3]}` : `${m[1]}-${m[2]}`;
}

export function googlePatentsUrl(compactOrRaw: string | null | undefined): string | null {
  const compact = normalizePatentPub(compactOrRaw);
  return compact ? `https://patents.google.com/patent/${compact}` : null;
}

/** All origin_url keys that should resolve to the same SourceDocument. */
export function patentIdAliases(raw: string | null | undefined): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const push = (v: string | null | undefined) => {
    const key = (v || "").trim();
    if (!key || seen.has(key)) return;
    seen.add(key);
    out.push(key.slice(0, 1024));
  };
  push(raw);
  const compact = normalizePatentPub(raw);
  push(compact);
  push(hyphenatedScpn(compact));
  push(googlePatentsUrl(compact));
  return out;
}

export function idsMatch(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  if (a === b) return true;
  const aa = new Set(patentIdAliases(a));
  return patentIdAliases(b).some((x) => aa.has(x));
}
