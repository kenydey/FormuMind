/**
 * AG Grid column definitions for the lab workbench — driven by objectives + DOE factors.
 */
import type { ColDef, ValueParserParams, ValueSetterParams } from "ag-grid-community";
import type { DOEPlan, ObjectiveSpec, WorkbenchRow } from "../api";

const RATING_OPTIONS = ["A", "B", "C", "D", "pass", "fail"];

export function numericParser(params: ValueParserParams) {
  const raw = String(params.newValue ?? "").trim().replace(/\s+/g, "");
  if (raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function isRatingObjective(obj: ObjectiveSpec): boolean {
  return obj.value_type === "rating" || /rating|grade|level/i.test(obj.metric);
}

export function buildWorkbenchColumnDefs(
  factorKeys: string[],
  objectives: ObjectiveSpec[],
): ColDef<WorkbenchRow>[] {
  const cols: ColDef<WorkbenchRow>[] = [
    { field: "id", headerName: "ID", editable: false, width: 64, pinned: "left" },
    {
      field: "status",
      headerName: "状态",
      editable: false,
      width: 96,
    },
  ];

  for (const key of factorKeys) {
    const short = key.replace(" (DGEBA)", "").slice(0, 12);
    cols.push({
      colId: `planned_${key}`,
      headerName: `计划 ${short}`,
      editable: false,
      valueGetter: (p) => p.data?.planned_params?.[key],
      cellStyle: { backgroundColor: "#f3f4f6", color: "#374151" },
      width: 108,
    });
    cols.push({
      colId: `actual_${key}`,
      headerName: `实际 ${short}`,
      editable: true,
      valueGetter: (p) => p.data?.actual_params?.[key],
      valueSetter: (p: ValueSetterParams<WorkbenchRow>) => {
        if (!p.data) return false;
        p.data.actual_params = { ...p.data.actual_params, [key]: p.newValue as number };
        return true;
      },
      valueParser: numericParser,
      cellStyle: { backgroundColor: "#eff6ff", color: "#1e40af" },
      width: 108,
    });
  }

  for (const obj of objectives) {
    const label = obj.display_name || obj.metric;
    const unitSuffix = obj.unit ? ` (${obj.unit})` : "";
    cols.push({
      colId: `meas_${obj.metric}`,
      headerName: `实测 ${label}${unitSuffix}`,
      editable: true,
      valueGetter: (p) => p.data?.measurements?.[obj.metric],
      valueSetter: (p: ValueSetterParams<WorkbenchRow>) => {
        if (!p.data) return false;
        p.data.measurements = { ...p.data.measurements, [obj.metric]: p.newValue as number | string };
        return true;
      },
      valueParser: isRatingObjective(obj) ? undefined : numericParser,
      cellEditor: isRatingObjective(obj) ? "agSelectCellEditor" : undefined,
      cellEditorParams: isRatingObjective(obj) ? { values: RATING_OPTIONS } : undefined,
      cellStyle: { backgroundColor: "#fefce8", color: "#713f12" },
      flex: 1,
      minWidth: 110,
    });
  }

  return cols;
}

export function factorKeysFromPlan(doePlan: DOEPlan, rows: WorkbenchRow[]): string[] {
  if (doePlan.factors.length > 0) return doePlan.factors.map((f) => f.name);
  const first = rows[0]?.planned_params ?? {};
  return Object.keys(first);
}

export function effectiveObjectives(
  requirementObjectives: ObjectiveSpec[],
  snapshot: ObjectiveSpec[] | undefined,
): ObjectiveSpec[] {
  if (snapshot && snapshot.length > 0) return snapshot;
  if (requirementObjectives.length > 0) return requirementObjectives;
  return [{ metric: "salt_spray_hours", weight: 1, direction: "maximize" }];
}
