import type { ProductDomain, Requirement } from "../api";

export type ConstraintKey =
  | "voc_limit_gpl"
  | "cure_temperature_c"
  | "ph_target"
  | "salt_spray_hours"
  | "film_weight_gsm"
  | "cleaning_efficiency";

export interface ConstraintDef {
  key: ConstraintKey;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  domains: ProductDomain[] | "all";
}

export const CONSTRAINT_CATALOG: ConstraintDef[] = [
  {
    key: "voc_limit_gpl",
    label: "VOC 上限 · VOC limit",
    unit: " g/L",
    min: 0,
    max: 700,
    step: 10,
    domains: "all",
  },
  {
    key: "cure_temperature_c",
    label: "固化温度上限 · Max cure temp",
    unit: "°C",
    min: 20,
    max: 300,
    step: 5,
    domains: ["anticorrosion_coating"],
  },
  {
    key: "ph_target",
    label: "pH 目标 · pH target",
    unit: "",
    min: 0,
    max: 14,
    step: 0.5,
    domains: ["degreaser"],
  },
  {
    key: "salt_spray_hours",
    label: "耐盐雾目标 · Salt spray target",
    unit: " h",
    min: 0,
    max: 3000,
    step: 50,
    domains: ["anticorrosion_coating", "surface_treatment"],
  },
  {
    key: "film_weight_gsm",
    label: "干膜重目标 · Dry film weight",
    unit: " g/m²",
    min: 0,
    max: 200,
    step: 5,
    domains: ["anticorrosion_coating"],
  },
  {
    key: "cleaning_efficiency",
    label: "清洗率目标 · Cleaning efficiency",
    unit: " %",
    min: 0,
    max: 100,
    step: 1,
    domains: ["degreaser"],
  },
];

export function constraintLabelForKey(key: ConstraintKey): string {
  const def = CONSTRAINT_CATALOG.find((c) => c.key === key);
  return def ? def.label.split(" · ")[0] : key;
}

export function constraintAppliesToDomain(def: ConstraintDef, domain: ProductDomain): boolean {
  return def.domains === "all" || def.domains.includes(domain);
}

export function defaultConstraintsForDomain(domain: ProductDomain): ConstraintKey[] {
  if (domain === "anticorrosion_coating") return ["voc_limit_gpl", "cure_temperature_c"];
  if (domain === "degreaser") return ["voc_limit_gpl", "ph_target"];
  return ["voc_limit_gpl"];
}

export function getConstraintValue(requirement: Requirement, key: ConstraintKey): number {
  const v = requirement[key];
  if (v == null) {
    const def = CONSTRAINT_CATALOG.find((c) => c.key === key);
    if (key === "ph_target") return 12;
    return def ? (def.min + def.max) / 2 : 0;
  }
  return v as number;
}
