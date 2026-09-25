import type { Requirement } from "../api";

/** Client-side preview of Requirement → recommend/DOE wiring (mirrors backend). */
export function previewRequirementEffectTrace(req: Requirement | null | undefined) {
  if (!req) return [];
  const items: Array<{
    field: string;
    kind: string;
    label: string;
    status: string;
    consumers?: string[];
    detail?: string;
  }> = [];

  const objectives = req.objectives ?? [];
  if (objectives.length) {
    for (const o of objectives) {
      const metric = o.metric || "";
      if (!metric) continue;
      items.push({
        field: `objectives.${metric}`,
        kind: "objective",
        label: metric,
        status: "wired",
        consumers: ["recommend", "doe"],
      });
    }
  } else {
    items.push({
      field: "objectives",
      kind: "objective",
      label: "(默认域目标)",
      status: "wired",
      consumers: ["recommend"],
    });
  }

  const levers = req.levers ?? [];
  if (levers.length) {
    for (const lev of levers) {
      const name = (lev as { name?: string }).name || "";
      if (!name) continue;
      items.push({
        field: `levers.${name}`,
        kind: "lever",
        label: name,
        status: "wired",
        consumers: ["doe"],
      });
    }
  } else {
    items.push({
      field: "levers",
      kind: "lever",
      label: "(域默认杠杆)",
      status: "wired",
      consumers: ["doe"],
      detail: "resolve_levers 回填",
    });
  }

  if (req.voc_limit_gpl != null) {
    items.push({
      field: "voc_limit_gpl",
      kind: "constraint",
      label: `VOC 上限=${req.voc_limit_gpl}`,
      status: "wired",
      consumers: ["validate", "recommend"],
    });
  }
  if (req.cure_temperature_c != null) {
    items.push({
      field: "cure_temperature_c",
      kind: "constraint",
      label: `固化温度上限=${req.cure_temperature_c}`,
      status: "wired",
      consumers: ["process"],
    });
  }
  if (req.ph_target != null) {
    items.push({
      field: "ph_target",
      kind: "constraint",
      label: `pH 目标=${req.ph_target}`,
      status: "display_only",
    });
  }
  if (req.notes?.trim()) {
    items.push({
      field: "notes",
      kind: "meta",
      label: "notes",
      status: "display_only",
      detail: "进入 LLM prompt，不进数值评分",
    });
  }
  for (const [k, v] of Object.entries(req.constraint_values || {})) {
    if (v == null) continue;
    items.push({
      field: `constraint_values.${k}`,
      kind: "constraint",
      label: `${k}=${v}`,
      status: "wired",
      consumers: ["recommend", "doe"],
    });
  }
  return items;
}
