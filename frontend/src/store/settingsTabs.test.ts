import { describe, expect, it } from "vitest";
import { normalizeSettingsTab, isRelocatedSettingsTab } from "./settingsTabs";

describe("normalizeSettingsTab (Wave 0 legacy mapping)", () => {
  it("keeps the four new tab ids", () => {
    for (const id of ["model", "capabilities", "prefs", "advanced"]) {
      expect(normalizeSettingsTab(id)).toBe(id);
    }
  });

  it("maps every old tab id to its new home", () => {
    expect(normalizeSettingsTab("llm")).toBe("model");
    expect(normalizeSettingsTab("api")).toBe("model");
    expect(normalizeSettingsTab("skills")).toBe("capabilities");
    expect(normalizeSettingsTab("connectors")).toBe("capabilities");
    expect(normalizeSettingsTab("notebooklm")).toBe("capabilities");
    expect(normalizeSettingsTab("recommend")).toBe("prefs");
    expect(normalizeSettingsTab("env")).toBe("advanced");
    expect(normalizeSettingsTab("deps")).toBe("advanced");
  });

  it("falls back to model for unknown / empty ids (never a blank tab)", () => {
    expect(normalizeSettingsTab("nope")).toBe("model");
    expect(normalizeSettingsTab("")).toBe("model");
    expect(normalizeSettingsTab(null)).toBe("model");
    expect(normalizeSettingsTab(undefined)).toBe("model");
  });

  it("flags relocated tabs for deep-link handling", () => {
    expect(isRelocatedSettingsTab("memory")).toBe(true);
    expect(isRelocatedSettingsTab("project")).toBe(true);
    expect(isRelocatedSettingsTab("org")).toBe(true);
    expect(isRelocatedSettingsTab("model")).toBe(false);
    expect(isRelocatedSettingsTab("llm")).toBe(false);
  });
});
