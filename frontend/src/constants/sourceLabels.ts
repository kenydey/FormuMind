/** Display names for search back-ends, shown on the live-search progress chips. */
const SOURCE_LABELS: Record<string, string> = {
  patents: "专利",
  serpapi_lit: "Scholar",
  openalex: "OpenAlex",
  arxiv: "arXiv",
  s2: "Semantic Scholar",
  chemlit: "化学文献",
  internet: "互联网",
  chemweb: "化学网页",
  notebooklm: "NotebookLM",
};

export function sourceLabel(name: string): string {
  return SOURCE_LABELS[name] || name;
}
