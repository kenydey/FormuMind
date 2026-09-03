/**
 * Objective contract helpers — mirror backend objective_contract.py for UI normalization.
 */
import type { ObjectiveSpec, Requirement } from "../api";
import { OBJECTIVE_METRIC } from "../api";

const METRIC_UNITS: Record<string, string> = {
  salt_spray_hours: "h",
  cleaning_efficiency: "%",
  cost_cny_per_kg: "CNY/kg",
  voc_gpl: "g/L",
  sustainability_idx: "",
  coating_weight_gsm: "g/m²",
  film_weight_gsm: "g/m²",
  ph_value: "",
};

const METRIC_LABELS: Record<string, string> = {
  salt_spray_hours: "耐盐雾 Salt Spray",
  cleaning_efficiency: "清洗率 Cleaning",
  cost_cny_per_kg: "成本 Cost",
  voc_gpl: "VOC",
  sustainability_idx: "可持续性",
  coating_weight_gsm: "膜重",
  film_weight_gsm: "干膜重",
  ph_value: "pH",
};

function shortId(): string {
  return Math.random().toString(16).slice(2, 10);
}

export function defaultUnit(metric: string): string {
  return METRIC_UNITS[metric] ?? "";
}

export function defaultDisplayName(metric: string): string {
  return METRIC_LABELS[metric] ?? metric.replace(/_/g, " ");
}

export function normalizeObjective(obj: ObjectiveSpec): ObjectiveSpec {
  return {
    ...obj,
    id: obj.id || obj.metric || shortId(),
    display_name: obj.display_name || defaultDisplayName(obj.metric),
    unit: obj.unit ?? defaultUnit(obj.metric),
    direction:
      obj.direction === "maximize" || obj.direction === "minimize" || obj.direction === "match_target"
        ? obj.direction
        : "maximize",
  };
}

export function normalizeObjectives(objectives: ObjectiveSpec[]): ObjectiveSpec[] {
  return objectives.map(normalizeObjective);
}

export function objectiveMetrics(req: Requirement): string[] {
  if (req.objectives?.length) return req.objectives.map((o) => o.metric);
  return [OBJECTIVE_METRIC[req.domain]];
}

export function extractMeasuredValues(
  measurements: Record<string, unknown> | undefined,
  metrics: string[]
): Record<string, number> | null {
  if (!metrics.length) return null;
  const primary = metrics[0];
  const pv = measurements?.[primary];
  if (pv === undefined || pv === null || pv === "" || Number.isNaN(Number(pv))) return null;
  const out: Record<string, number> = {};
  for (const m of metrics) {
    const v = measurements?.[m];
    if (v !== undefined && v !== null && v !== "" && !Number.isNaN(Number(v))) {
      out[m] = Number(v);
    }
  }
  return Object.keys(out).length ? out : null;
}
