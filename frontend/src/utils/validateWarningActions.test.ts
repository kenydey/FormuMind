import { describe, expect, it } from "vitest";
import {
  classifyValidateWarning,
  classifyValidateWarnings,
} from "./validateWarningActions";

describe("classifyValidateWarning", () => {
  it("tags missing CAS / SMILES as catalog", () => {
    expect(classifyValidateWarning("A2: no CAS numbers resolved for ingredients")).toBe(
      "catalog",
    );
    expect(
      classifyValidateWarning("环氧乳液: SMILES 'xyz' 无法被 RDKit 解析（结构幻觉），已清空待重填"),
    ).toBe("catalog");
    expect(classifyValidateWarning("Rec: missing CAS for Water, Xylene")).toBe("catalog");
  });

  it("tags REACH / SVHC / RoHS as compliance", () => {
    expect(
      classifyValidateWarning("Zinc molybdate: REACH SVHC 候选 钼酸锌，商业化前需确认合规状态"),
    ).toBe("compliance");
    expect(classifyValidateWarning("Lead: 检出 RoHS 受限物质")).toBe("compliance");
  });

  it("tags weight / stoichiometry as weight", () => {
    expect(classifyValidateWarning("A2: ingredient weights sum to 88.0% (expected ~100%)")).toBe(
      "weight",
    );
    expect(classifyValidateWarning("A2: 环氧/胺当量比 2.10 偏离化学计量窗口 0.6-1.8")).toBe(
      "weight",
    );
  });
});

describe("classifyValidateWarnings", () => {
  it("suggests propose_missing + open_materials for catalog gaps", () => {
    const r = classifyValidateWarnings([
      "A2: no CAS numbers resolved for ingredients",
      "Water: 通过网络检索补全 CAS 7732-18-5",
    ]);
    expect(r.hasCatalog).toBe(true);
    expect(r.actions).toEqual(["propose_missing", "open_materials"]);
  });

  it("suggests open_materials for compliance-only (no auto-clear)", () => {
    const r = classifyValidateWarnings([
      "Primer: Zinc molybdate: REACH SVHC 候选，商业化前需确认合规状态",
    ]);
    expect(r.hasCompliance).toBe(true);
    expect(r.actions).toEqual(["open_materials"]);
    expect(r.actions).not.toContain("propose_missing");
  });

  it("returns empty actions when no warnings", () => {
    expect(classifyValidateWarnings([]).actions).toEqual([]);
  });
});
