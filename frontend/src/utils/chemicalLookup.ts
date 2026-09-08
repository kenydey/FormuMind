/** Format /api/chemical/lookup source for MaterialsPanel status line. */

export type ChemicalLookupResultLike = {
  source?: string;
  surechembl?: {
    chemical_id?: string | null;
    global_frequency?: number | null;
  } | null;
};

export function formatChemicalLookupSourceMsg(r: ChemicalLookupResultLike): string {
  if (r.source === "surechembl") {
    const cid = r.surechembl?.chemical_id;
    const freq = r.surechembl?.global_frequency;
    const bits = [
      "已自动填充",
      "来源 专利化学库 (SureChEMBL)",
      cid ? `ID ${cid}` : null,
      freq != null ? `频次 ${freq}` : null,
    ].filter(Boolean);
    return `${bits.join(" · ")} · 公开专利标注，仅供研发参考`;
  }
  const label =
    r.source === "catalog"
      ? "材料目录"
      : r.source?.startsWith("pubchem")
        ? "PubChem"
        : r.source || "lookup";
  return `已自动填充 · 来源 ${label}`;
}
