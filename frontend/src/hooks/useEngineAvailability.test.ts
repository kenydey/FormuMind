import { describe, expect, it } from "vitest";
import { engineOk, type EngineAvailability } from "./useEngineAvailability";

describe("engineOk", () => {
  it("treats missing probe as available (optimistic)", () => {
    const map: EngineAvailability = {};
    expect(engineOk(map, "baybe")).toBe(true);
  });

  it("respects available=false", () => {
    const map: EngineAvailability = { baybe: false, pydoe: true };
    expect(engineOk(map, "baybe")).toBe(false);
    expect(engineOk(map, "pydoe")).toBe(true);
  });
});
