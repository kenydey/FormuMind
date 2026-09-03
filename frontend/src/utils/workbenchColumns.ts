/**
 * AG Grid column definitions for the lab workbench — driven by objectives + DOE factors.
 *
 * Phase 1+ enhancements:
 *  - RAG conditional formatting on measurement columns
 *  - Column groups: "DOE 计划" / "实验实际值" / "性能指标"
 *  - Number editor with range validation from DOE factor bounds
 */
import type { ColDef, ColGroupDef, ValueParserParams, ValueSetterParams } from "ag-grid-community";
import type { DOEPlan, ObjectiveSpec, WorkbenchRow } from "../api";

const RATING_OPTIONS = ["A", "B", "C", "D", "pass", "fail"];
/** 后端 row.status 枚举（P1: 状态下拉编辑；Completed 触发回灌训练） */
export const WORKBENCH_STATUS_OPTIONS = [
  "Pending",
  "In Progress",
  "Completed",
  "Blocked",
] as const;

export function numericParser(params: ValueParserParams) {
  const raw = String(params.newValue ?? "").trim().replace(/\s+/g, "");
  if (raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function isRatingObjective(obj: ObjectiveSpec): boolean {
  return obj.value_type === "rating" || /rating|grade|level/i.test(obj.metric);
}

// ── RAG conditional formatting (Phase 1.1) ─────────────────────────────

/** spec 窗口文本（tooltip 用）：≥800 h / 8–15 g/m² / 目标 12.5 */
export function specText(obj: ObjectiveSpec): string {
  const unit = obj.unit ? ` ${obj.unit}` : "";
  if (obj.ref_min != null && obj.ref_max != null) return `${obj.ref_min}–${obj.ref_max}${unit}`;
  if (obj.ref_min != null)
    return obj.direction === "minimize"
      ? `≤ ${obj.ref_min}${unit}`
      : `≥ ${obj.ref_min}${unit}`;
  if (obj.ref_max != null) return `≤ ${obj.ref_max}${unit}`;
  if (obj.target_value != null) {
    const dir =
      obj.direction === "maximize" ? "≥" : obj.direction === "minimize" ? "≤" : "≈";
    return `${dir} ${obj.target_value}${unit}`;
  }
  return "";
}

/** 当前值是否满足 spec（含无 spec 时的目标方向判定） */
export function valueMeetsSpec(obj: ObjectiveSpec, value: unknown): boolean | null {
  const v = Number(value);
  if (value == null || value === "" || Number.isNaN(v)) return null; // 空值
  if (obj.ref_min != null && v < obj.ref_min) return false;
  if (obj.ref_max != null && v > obj.ref_max) return false;
  if (obj.ref_min == null && obj.ref_max == null && obj.target_value != null) {
    if (obj.direction === "maximize") return v >= obj.target_value;
    if (obj.direction === "minimize") return v <= obj.target_value;
    return Math.abs(v - obj.target_value) / Math.max(Math.abs(obj.target_value), 1) <= 0.1;
  }
  return true;
}

function ragStyle(
  obj: ObjectiveSpec,
): (params: { value: unknown }) => { backgroundColor?: string; color?: string } {
  const dir = obj.direction;
  const target = obj.target_value;
  return (params) => {
    const v = Number(params.value);
    if (Number.isNaN(v) || params.value === "" || params.value == null) return {};
    let ok = false;
    if (obj.ref_min != null && v < obj.ref_min) ok = false;
    else if (obj.ref_max != null && v > obj.ref_max) ok = false;
    else if (obj.ref_min == null && obj.ref_max == null) {
      if (dir === "maximize") ok = v >= (target ?? Number.NEGATIVE_INFINITY);
      else if (dir === "minimize") ok = v <= (target ?? Number.POSITIVE_INFINITY);
      else if (dir === "match_target")
        ok = target != null && Math.abs(v - target) / Math.max(Math.abs(target), 1) <= 0.1;
    } else ok = true;
    return ok
      ? { backgroundColor: "#dcfce7", color: "#166534" }
      : { backgroundColor: "#fef2f2", color: "#991b1b" };
  };
}

// ── Column group builder (Phase 1.2) ───────────────────────────────────

export function buildWorkbenchColumnDefs(
  factorKeys: string[],
  objectives: ObjectiveSpec[],
  doePlan?: DOEPlan,             // ← Phase 1.3: needed for range validation
  attachmentCounts?: Record<number, number>,  // ← Phase 2.1: attachment badge
): (ColDef<WorkbenchRow> | ColGroupDef<WorkbenchRow>)[] {
  const factorMap = new Map((doePlan?.factors ?? []).map((f) => [f.name, f]));

  const cols: (ColDef<WorkbenchRow> | ColGroupDef<WorkbenchRow>)[] = [
    { field: "id", headerName: "ID", editable: false, width: 64, pinned: "left" },
    {
      colId: "attachments",
      headerName: "📎",
      editable: false,
      width: 52,
      pinned: "left",
      valueGetter: (p) => attachmentCounts?.[p.data?.id ?? -1] ?? 0,
      cellStyle: { textAlign: "center" },
    },
    {
      field: "status",
      headerName: "状态",
      editable: true,
      width: 128,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: WORKBENCH_STATUS_OPTIONS },
    },
  ];

  // ── "DOE 计划" 列组 ──────────────────────────────────────────────
  const plannedCols: ColDef<WorkbenchRow>[] = factorKeys.map((key) => {
    const short = key.replace(" (DGEBA)", "").slice(0, 12);
    return {
      colId: `planned_${key}`,
      headerName: short,
      editable: false,
      valueGetter: (p) => p.data?.planned_params?.[key],
      cellStyle: { backgroundColor: "#f3f4f6", color: "#374151" },
      width: 108,
    };
  });
  cols.push({ headerName: "DOE 计划", children: plannedCols });

  // ── "实验实际值" 列组 ────────────────────────────────────────────
  const actualCols: ColDef<WorkbenchRow>[] = factorKeys.map((key) => {
    const short = key.replace(" (DGEBA)", "").slice(0, 12);
    const factor = factorMap.get(key);
    const base: ColDef<WorkbenchRow> = {
      colId: `actual_${key}`,
      headerName: short,
      editable: true,
      cellEditor: "agNumberCellEditor",
      cellEditorParams: {
        precision: 2,
        showStepperButtons: false,
      },
      valueGetter: (p) => p.data?.actual_params?.[key],
      valueSetter: (p: ValueSetterParams<WorkbenchRow>) => {
        if (!p.data) return false;
        p.data.actual_params = {
          ...p.data.actual_params,
          [key]: p.newValue as number,
        };
        return true;
      },
      valueParser: numericParser,
      cellStyle: { backgroundColor: "#eff6ff", color: "#1e40af" },
      width: 108,
    };
    // Range validation from DOE plan factor bounds (Phase 1.3)
    if (factor) {
      base.cellEditorParams = {
        ...base.cellEditorParams,
        min: factor.low,
        max: factor.high,
      };
    }
    return base;
  });
  cols.push({ headerName: "实验实际值", children: actualCols });

  // ── "性能指标" 列组 ───────────────────────────────────────────────
  const measCols: ColDef<WorkbenchRow>[] = objectives.map((obj) => {
    const label = obj.display_name || obj.metric;
    const unitSuffix = obj.unit ? ` (${obj.unit})` : "";
    return {
      colId: `meas_${obj.metric}`,
      headerName: `${label}${unitSuffix}`,
      editable: true,
      valueGetter: (p) => p.data?.measurements?.[obj.metric],
      valueSetter: (p: ValueSetterParams<WorkbenchRow>) => {
        if (!p.data) return false;
        p.data.measurements = {
          ...p.data.measurements,
          [obj.metric]: p.newValue as number | string,
        };
        return true;
      },
      valueParser: isRatingObjective(obj) ? undefined : numericParser,
      cellEditor: isRatingObjective(obj) ? "agSelectCellEditor" : undefined,
      cellEditorParams: isRatingObjective(obj)
        ? { values: RATING_OPTIONS }
        : undefined,
      cellStyle: ragStyle(obj),  // ← Phase 1.1: RAG coloring
      tooltipValueGetter: (p: { value?: unknown }) => {
        const spec = specText(obj);
        const pass = valueMeetsSpec(obj, p.value);
        const state =
          p.value == null || p.value === ""
            ? "待回填"
            : pass === true
              ? "满足规格"
              : pass === false
                ? "超出规格"
                : "";
        return `${obj.display_name || obj.metric}${spec ? `\n规格 ${spec}` : ""}${
          state ? `\n${state}` : ""
        }`;
      },
      flex: 1,
      minWidth: 110,
    };
  });
  cols.push({ headerName: "性能指标", children: measCols });

  return cols;
}

export function factorKeysFromPlan(
  doePlan: DOEPlan,
  rows: WorkbenchRow[],
): string[] {
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
