/**
 * Lab-ledger column construction.
 *
 * These decide what the workbench grid can record. A metric that never gets a
 * column can never be measured, and the run silently stays incomplete — so the
 * fallback behaviour when objectives are missing matters as much as the happy
 * path.
 */
import { describe, expect, it } from "vitest";
import type { DOEPlan, ObjectiveSpec, WorkbenchRow } from "../api";
import {
  WORKBENCH_STATUS_OPTIONS,
  buildWorkbenchColumnDefs,
  effectiveObjectives,
  factorKeysFromPlan,
  numericParser,
  specText,
  valueMeetsSpec,
} from "./workbenchColumns";

const parse = (value: unknown) =>
  numericParser({ newValue: value } as never);

const objective = (over: Partial<ObjectiveSpec> = {}): ObjectiveSpec =>
  ({ metric: "salt_spray_hours", weight: 1, direction: "maximize", ...over }) as ObjectiveSpec;

describe("numericParser", () => {
  it("parses plain and decimal numbers", () => {
    expect(parse("720")).toBe(720);
    expect(parse("3.25")).toBe(3.25);
    expect(parse("-5")).toBe(-5);
  });

  it("keeps a genuine zero", () => {
    // Zero is a real measurement; returning null would mark the cell unmeasured.
    expect(parse("0")).toBe(0);
  });

  it("returns null for a cleared cell rather than zero", () => {
    expect(parse("")).toBeNull();
    expect(parse("   ")).toBeNull();
    expect(parse(null)).toBeNull();
    expect(parse(undefined)).toBeNull();
  });

  it("tolerates whitespace inside the value", () => {
    // Pasting from a spreadsheet can bring stray spaces along.
    expect(parse(" 1 200 ")).toBe(1200);
  });

  it("returns null for text instead of NaN", () => {
    // NaN would reach the backend and fail validation there instead of here.
    expect(parse("abc")).toBeNull();
    expect(parse("12abc")).toBeNull();
  });
});

describe("factorKeysFromPlan", () => {
  it("uses the declared plan factors", () => {
    const plan = { factors: [{ name: "Zinc phosphate" }, { name: "Talc" }] } as DOEPlan;
    expect(factorKeysFromPlan(plan, [])).toEqual(["Zinc phosphate", "Talc"]);
  });

  it("falls back to the first row's planned params", () => {
    // A campaign adopted from an external plan can arrive with no factor list,
    // and the grid still has to know which columns to show.
    const plan = { factors: [] } as unknown as DOEPlan;
    const rows = [{ planned_params: { A: 1, B: 2 } }] as unknown as WorkbenchRow[];
    expect(factorKeysFromPlan(plan, rows)).toEqual(["A", "B"]);
  });

  it("returns nothing when there is neither", () => {
    expect(factorKeysFromPlan({ factors: [] } as unknown as DOEPlan, [])).toEqual([]);
  });
});

describe("effectiveObjectives", () => {
  it("prefers the campaign snapshot over the current requirement", () => {
    // The snapshot is what the campaign was actually planned against; letting
    // a later requirement edit change the ledger columns would orphan the
    // measurements already recorded under the old ones.
    const snapshot = [objective({ metric: "cost_cny_per_kg" })];
    const current = [objective({ metric: "salt_spray_hours" })];
    expect(effectiveObjectives(current, snapshot)).toBe(snapshot);
  });

  it("uses the requirement when there is no snapshot", () => {
    const current = [objective({ metric: "cleaning_efficiency" })];
    expect(effectiveObjectives(current, undefined)).toBe(current);
    expect(effectiveObjectives(current, [])).toBe(current);
  });

  it("always yields at least one objective", () => {
    // An empty column set would make the grid unable to record anything.
    const fallback = effectiveObjectives([], undefined);
    expect(fallback).toHaveLength(1);
    expect(fallback[0].metric).toBe("salt_spray_hours");
  });
});

describe("buildWorkbenchColumnDefs", () => {
  it("pins the identity columns and appends one per factor and objective", () => {
    const cols = buildWorkbenchColumnDefs(
      ["Zinc phosphate"],
      [objective(), objective({ metric: "cost_cny_per_kg" })]
    );
    const fields = cols.map((c) => ("field" in c ? c.field : c.headerName));
    expect(fields[0]).toBe("id");
    const idCol = cols[0];
    expect("pinned" in idCol ? idCol.pinned : undefined).toBe("left");
    expect(JSON.stringify(cols)).toContain("Zinc phosphate");
    expect(JSON.stringify(cols)).toContain("salt_spray_hours");
    expect(JSON.stringify(cols)).toContain("cost_cny_per_kg");
  });

  it("keeps identity columns read-only", () => {
    const cols = buildWorkbenchColumnDefs([], [objective()]);
    const id = cols.find((c) => "field" in c && c.field === "id");
    const status = cols.find((c) => "field" in c && c.field === "status");
    expect((id as Record<string, unknown>)?.editable).toBe(false);
    // P1: status 可下拉编辑（Completed 触发回灌训练）——不再是只读徽章
    expect((status as Record<string, unknown>)?.editable).toBe(true);
  });

  it("status column offers the workbench status enum via select editor", () => {
    const cols = buildWorkbenchColumnDefs([], [objective()]);
    const status = cols.find((c) => "field" in c && c.field === "status");
    const params = (status as Record<string, unknown>)?.cellEditorParams as {
      values: string[];
    };
    expect((status as Record<string, unknown>)?.cellEditor).toBe("agSelectCellEditor");
    expect(params?.values).toEqual(WORKBENCH_STATUS_OPTIONS);
  });

  it("measurement columns carry spec tooltips and RAG verdict helpers", () => {
    // specText / valueMeetsSpec drive tooltip + detail-bar verdicts
    const obj = {
      metric: "salt_spray_hours",
      display_name: "盐雾",
      unit: "h",
      direction: "maximize" as const,
      ref_min: 400,
      weight: 1,
    };
    expect(specText(obj)).toContain("≥ 400");
    expect(specText(obj)).toContain("h");
    expect(valueMeetsSpec(obj, 500)).toBe(true);
    expect(valueMeetsSpec(obj, 100)).toBe(false);
    expect(valueMeetsSpec(obj, null)).toBeNull();
    expect(valueMeetsSpec(obj, "")).toBeNull();
  });

  it("builds a usable grid with no factors at all", () => {
    const cols = buildWorkbenchColumnDefs([], [objective()]);
    expect(cols.length).toBeGreaterThan(0);
  });
});
