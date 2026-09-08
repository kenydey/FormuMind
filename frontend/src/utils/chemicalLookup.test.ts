import { describe, expect, it } from "vitest";
import { formatChemicalLookupSourceMsg } from "./chemicalLookup";

describe("formatChemicalLookupSourceMsg", () => {
  it("labels SureChEMBL hits with id and disclaimer", () => {
    const msg = formatChemicalLookupSourceMsg({
      source: "surechembl",
      surechembl: { chemical_id: "42", global_frequency: 7 },
    });
    expect(msg).toContain("SureChEMBL");
    expect(msg).toContain("ID 42");
    expect(msg).toContain("频次 7");
    expect(msg).toContain("仅供研发参考");
  });

  it("maps catalog and pubchem sources", () => {
    expect(formatChemicalLookupSourceMsg({ source: "catalog" })).toContain("材料目录");
    expect(formatChemicalLookupSourceMsg({ source: "pubchem" })).toContain("PubChem");
  });
});
