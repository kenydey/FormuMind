/**
 * Objective contract helpers.
 *
 * These mirror the backend's objective_contract.py and decide the column set
 * for the DOE grid and the workbench ledger, so a drift here shows up as
 * missing or mislabelled columns rather than as an error.
 */
import { describe, expect, it } from "vitest";
import type { ObjectiveSpec, Requirement } from "../api";
import {
  defaultDisplayName,
  defaultUnit,
  extractMeasuredValues,
  normalizeObjective,
  normalizeObjectives,
  objectiveMetrics,
} from "./objectiveContract";

const spec = (over: Partial<ObjectiveSpec> = {}): ObjectiveSpec =>
  ({ metric: "salt_spray_hours", ...over }) as ObjectiveSpec;

describe("defaultUnit / defaultDisplayName", () => {
  it("knows the built-in metrics", () => {
    expect(defaultUnit("salt_spray_hours")).toBe("h");
    expect(defaultUnit("voc_gpl")).toBe("g/L");
    expect(defaultDisplayName("cost_cny_per_kg")).toContain("Cost");
  });

  it("degrades readably for a custom metric", () => {
    // Custom metrics are user-defined, so an unknown key must render as a
    // label rather than as a raw snake_case key or an empty cell.
    expect(defaultUnit("my_custom_metric")).toBe("");
    expect(defaultDisplayName("my_custom_metric")).toBe("my custom metric");
  });
});

describe("normalizeObjective", () => {
  it("fills id, display name and unit from the metric", () => {
    const out = normalizeObjective(spec());
    expect(out.id).toBe("salt_spray_hours");
    expect(out.display_name).toContain("耐盐雾");
    expect(out.unit).toBe("h");
  });

  it("does not overwrite values the user set", () => {
    const out = normalizeObjective(
      spec({ id: "mine", display_name: "My label", unit: "days" })
    );
    expect(out).toMatchObject({ id: "mine", display_name: "My label", unit: "days" });
  });

  it("keeps an explicitly empty unit", () => {
    // `unit ?? default` — an empty string is a deliberate choice for a
    // dimensionless metric and must survive.
    expect(normalizeObjective(spec({ unit: "" })).unit).toBe("");
  });

  it("repairs an invalid direction rather than passing it through", () => {
    expect(normalizeObjective(spec({ direction: "sideways" as never })).direction).toBe("maximize");
    expect(normalizeObjective(spec({ direction: "minimize" })).direction).toBe("minimize");
    expect(normalizeObjective(spec({ direction: "match_target" })).direction).toBe("match_target");
  });

  it("generates an id when the metric is blank", () => {
    expect(normalizeObjective(spec({ metric: "" })).id).toBeTruthy();
  });
});

describe("normalizeObjectives", () => {
  it("normalizes every entry", () => {
    const out = normalizeObjectives([spec(), spec({ metric: "voc_gpl" })]);
    expect(out.map((o) => o.unit)).toEqual(["h", "g/L"]);
  });

  it("handles an empty list", () => {
    expect(normalizeObjectives([])).toEqual([]);
  });
});

describe("objectiveMetrics", () => {
  it("uses the declared objectives in order", () => {
    const req = {
      domain: "anticorrosion_coating",
      objectives: [spec({ metric: "cost_cny_per_kg" }), spec()],
    } as unknown as Requirement;
    expect(objectiveMetrics(req)).toEqual(["cost_cny_per_kg", "salt_spray_hours"]);
  });

  it("falls back to the domain default", () => {
    const req = { domain: "degreaser", objectives: [] } as unknown as Requirement;
    expect(objectiveMetrics(req)).toEqual(["cleaning_efficiency"]);
  });
});

describe("extractMeasuredValues", () => {
  const metrics = ["salt_spray_hours", "cost_cny_per_kg"];

  it("returns the numeric subset", () => {
    expect(extractMeasuredValues({ salt_spray_hours: 720, cost_cny_per_kg: 18 }, metrics)).toEqual({
      salt_spray_hours: 720,
      cost_cny_per_kg: 18,
    });
  });

  it("requires the primary metric before treating a row as measured", () => {
    // A row with only a secondary value is not a completed measurement; the
    // workbench uses this to decide whether a run counts as done.
    expect(extractMeasuredValues({ cost_cny_per_kg: 18 }, metrics)).toBeNull();
    expect(extractMeasuredValues({ salt_spray_hours: "" }, metrics)).toBeNull();
    expect(extractMeasuredValues({ salt_spray_hours: "abc" }, metrics)).toBeNull();
  });

  it("coerces numeric strings from the grid", () => {
    // AG Grid hands back strings from a text cell.
    expect(extractMeasuredValues({ salt_spray_hours: "720" }, metrics)).toEqual({
      salt_spray_hours: 720,
    });
  });

  it("drops blank secondary values instead of writing zero", () => {
    const out = extractMeasuredValues({ salt_spray_hours: 720, cost_cny_per_kg: "" }, metrics);
    expect(out).toEqual({ salt_spray_hours: 720 });
  });

  it("keeps a genuine zero", () => {
    expect(extractMeasuredValues({ salt_spray_hours: 0 }, metrics)).toEqual({
      salt_spray_hours: 0,
    });
  });

  it("returns null for no metrics or no measurements", () => {
    expect(extractMeasuredValues({ salt_spray_hours: 720 }, [])).toBeNull();
    expect(extractMeasuredValues(undefined, metrics)).toBeNull();
  });
});
