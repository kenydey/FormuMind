/** Classify formulation validate warnings into actionable Hub categories. */

export type ValidateWarningKind = "catalog" | "compliance" | "weight" | "other";

export type ValidateBannerAction = "propose_missing" | "open_materials";

const CATALOG_RE =
  /CAS|SMILES|MF|目录|材料库|网络检索补全|无法被 RDKit|待重填|no CAS|missing CAS|missing MF/i;
const COMPLIANCE_RE = /SVHC|REACH|RoHS|合规/i;
const WEIGHT_RE = /weight|weights sum|weight_pct|当量比|化学计量|~100%/i;

export function classifyValidateWarning(warning: string): ValidateWarningKind {
  const w = (warning || "").trim();
  if (!w) return "other";
  if (COMPLIANCE_RE.test(w)) return "compliance";
  if (CATALOG_RE.test(w)) return "catalog";
  if (WEIGHT_RE.test(w)) return "weight";
  return "other";
}

export function classifyValidateWarnings(warnings: string[]): {
  kinds: Record<ValidateWarningKind, number>;
  actions: ValidateBannerAction[];
  hasCompliance: boolean;
  hasCatalog: boolean;
} {
  const kinds: Record<ValidateWarningKind, number> = {
    catalog: 0,
    compliance: 0,
    weight: 0,
    other: 0,
  };
  for (const w of warnings || []) {
    kinds[classifyValidateWarning(w)] += 1;
  }
  const actions: ValidateBannerAction[] = [];
  if (kinds.catalog > 0) actions.push("propose_missing");
  // Materials library is useful for catalog gaps and compliance review.
  if (kinds.catalog > 0 || kinds.compliance > 0 || kinds.other > 0 || kinds.weight > 0) {
    actions.push("open_materials");
  }
  return {
    kinds,
    actions,
    hasCompliance: kinds.compliance > 0,
    hasCatalog: kinds.catalog > 0,
  };
}
