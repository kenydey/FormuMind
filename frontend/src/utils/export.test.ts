/**
 * CSV export.
 *
 * These files leave the app and get opened in Excel, so a quoting bug is not
 * a rendering glitch — it silently shifts every column after the offending
 * cell, and the recipient has no way to tell.
 */
import { describe, expect, it } from "vitest";
import type { Formulation } from "../api";
import { formulaToCsv, leaderboardToCsv } from "./export";

const form = (over: Partial<Formulation> = {}): Formulation =>
  ({
    name: "Primer A",
    domain: "anticorrosion_coating",
    ingredients: [
      { name: "Epoxy", role: "resin", formula: "C21H24O4", weight_pct: 60 },
      { name: "Talc", role: "filler", formula: null, weight_pct: 40 },
    ],
    predicted: { salt_spray_hours: 800, cost_cny_per_kg: 20 },
    predicted_std: { salt_spray_hours: 40 },
    score: 0.87,
    warnings: [],
    ...over,
  }) as unknown as Formulation;

describe("formulaToCsv", () => {
  it("emits the header, ingredients and predictions", () => {
    const csv = formulaToCsv(form());
    expect(csv).toContain("# Formulation,Primer A");
    expect(csv).toContain("# Score,0.87");
    expect(csv).toContain("Ingredient,Role,Formula,Weight %");
    expect(csv).toContain("Epoxy,resin,C21H24O4,60");
    expect(csv).toContain("salt_spray_hours,800,40");
  });

  it("leaves the std cell empty rather than writing undefined", () => {
    // "undefined" in a numeric column breaks the spreadsheet downstream.
    expect(formulaToCsv(form())).toContain("cost_cny_per_kg,20,");
    expect(formulaToCsv(form())).not.toContain("undefined");
  });

  it("renders a missing formula as an empty cell", () => {
    expect(formulaToCsv(form())).toContain("Talc,filler,,40");
  });

  it("quotes cells containing a comma so columns do not shift", () => {
    const csv = formulaToCsv(
      form({
        ingredients: [
          { name: "Solvent, mixed", role: "solvent", formula: null, weight_pct: 10 },
        ],
      } as Partial<Formulation>)
    );
    expect(csv).toContain('"Solvent, mixed",solvent,,10');
  });

  it("escapes embedded quotes by doubling them", () => {
    const csv = formulaToCsv(
      form({
        ingredients: [
          { name: 'Grade "A"', role: "resin", formula: null, weight_pct: 10 },
        ],
      } as Partial<Formulation>)
    );
    expect(csv).toContain('"Grade ""A""",resin,,10');
  });

  it("omits the score line when there is no score", () => {
    expect(formulaToCsv(form({ score: null }))).not.toContain("# Score");
  });

  it("handles a formulation with no ingredients", () => {
    expect(() => formulaToCsv(form({ ingredients: [] }))).not.toThrow();
  });
});

describe("leaderboardToCsv", () => {
  it("ranks rows from 1 and resolves the domain's primary metric", () => {
    const csv = leaderboardToCsv([form({ name: "Best" }), form({ name: "Second" })]);
    const rows = csv.split("\n");
    expect(rows[0]).toContain("Rank,Name,Domain,Score");
    expect(rows[1]).toContain("1,Best,anticorrosion_coating");
    expect(rows[1]).toContain("salt_spray_hours");
    expect(rows[2]).toContain("2,Second");
  });

  it("appends the per-ingredient detail block", () => {
    const csv = leaderboardToCsv([form()]);
    expect(csv).toContain("# Ingredient details");
    expect(csv).toContain("Rank,Name,Ingredient,Role,Weight %");
    expect(csv).toContain("1,Primer A,Epoxy,resin,60");
  });

  it("joins warnings into one cell instead of breaking the row", () => {
    const csv = leaderboardToCsv([form({ warnings: ["too much filler", "VOC high"] })]);
    expect(csv).toContain("too much filler; VOC high");
  });

  it("quotes a joined warning list containing a comma", () => {
    const csv = leaderboardToCsv([form({ warnings: ["a, b", "c"] })]);
    expect(csv).toContain('"a, b; c"');
  });

  it("writes an empty cell for a metric the domain does not predict", () => {
    const csv = leaderboardToCsv([form({ predicted: { salt_spray_hours: 800 } })]);
    expect(csv).not.toContain("undefined");
  });

  it("produces just a header for an empty leaderboard", () => {
    const csv = leaderboardToCsv([]);
    expect(csv.split("\n")[0]).toContain("Rank,Name");
    expect(csv).toContain("# Ingredient details");
  });
});
