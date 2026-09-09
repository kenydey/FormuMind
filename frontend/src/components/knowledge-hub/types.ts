import type { Evidence } from "../../api";

/** Unified row for Knowledge Hub materials tab. */
export type HubMaterialRow = {
  row_key: string;
  kind: "session" | "kb";
  title: string;
  source: string;
  identifier: string;
  url?: string | null;
  url_alt?: string | null;
  oa_pdf_url?: string | null;
  is_oa?: boolean | null;
  assignee?: string | null;
  pub_date?: string | null;
  snippet?: string;
  relevance?: number;
  kb_status?: string;
  source_id?: string | null;
  selected?: boolean;
  evidence?: Evidence;
};

export type KnowledgeHubTab = "materials" | "wiki" | "graph" | "reports";

export function resolveOpenUrl(row: HubMaterialRow): string | null {
  const candidates = [row.oa_pdf_url, row.url, row.url_alt];
  for (const u of candidates) {
    const s = (u || "").trim();
    if (s.startsWith("http://") || s.startsWith("https://")) return s;
  }
  return null;
}
