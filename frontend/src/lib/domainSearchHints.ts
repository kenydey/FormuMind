/** Mirrors backend DOMAIN_SEARCH_HINTS — domain-adaptive playbook search queries. */
export const DOMAIN_SEARCH_HINTS: Record<string, string> = {
  anticorrosion_coating: "防腐涂料 底漆 缓蚀",
  degreaser: "脱脂剂 表面活性剂 清洗",
  surface_treatment: "表面处理 转化膜 附着力",
  autodeposition_coating: "自沉积涂料 酸致凝聚",
};

export const DEFAULT_SEARCH_HINT = "配方 组分 工艺";

export function resolveSearchHint(
  presets: Record<string, unknown> | undefined | null,
  domain?: string | null,
): string | null {
  const p = presets || {};
  const mode = typeof p.search_hint_mode === "string" ? p.search_hint_mode.trim() : "";
  const explicit = typeof p.search_hint === "string" ? p.search_hint.trim() : "";
  if (explicit && !mode) return explicit;

  if (mode === "domain" || mode === "domain_literature") {
    const key = (domain || "").trim();
    const base = DOMAIN_SEARCH_HINTS[key] || DEFAULT_SEARCH_HINT;
    return mode === "domain_literature" ? `${base} 综述` : base;
  }
  return explicit || null;
}
