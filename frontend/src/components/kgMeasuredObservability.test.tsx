import { describe, expect, it } from "vitest";
import {
  cardMeasuredChip,
  formatKgWrittenHint,
} from "./kgMeasuredObservability";

describe("formatKgWrittenHint", () => {
  it("formats positive counts", () => {
    expect(formatKgWrittenHint(3)).toBe("KG 回流 3 条实测证据");
    expect(formatKgWrittenHint(1)).toBe("KG 回流 1 条实测证据");
  });

  it("returns null for empty / invalid", () => {
    expect(formatKgWrittenHint(0)).toBeNull();
    expect(formatKgWrittenHint(null)).toBeNull();
    expect(formatKgWrittenHint(undefined)).toBeNull();
    expect(formatKgWrittenHint(Number.NaN)).toBeNull();
  });
});

describe("cardMeasuredChip", () => {
  it("prefers metric-aware hits over binary materials", () => {
    const chip = cardMeasuredChip(
      [
        {
          material: "GPTMS",
          metric: "salt_spray_hours",
          quality: "good",
          value: 720,
        },
      ],
      ["Zinc phosphate"],
    );
    expect(chip).not.toBeNull();
    expect(chip!.quality).toBe("good");
    expect(chip!.label).toBe("实测加成");
    expect(chip!.title).toContain("GPTMS");
  });

  it("falls back to binary measured materials", () => {
    const chip = cardMeasuredChip([], ["GPTMS", "Zinc"]);
    expect(chip).not.toBeNull();
    expect(chip!.quality).toBe("binary");
    expect(chip!.label).toBe("实测");
    expect(chip!.title).toContain("GPTMS");
  });

  it("returns null when no measured signal", () => {
    expect(cardMeasuredChip([], [])).toBeNull();
    expect(cardMeasuredChip(undefined, undefined)).toBeNull();
  });

  it("marks poor quality for weak hits", () => {
    const chip = cardMeasuredChip(
      [{ material: "A", metric: "voc_gpl", quality: "poor" }],
      null,
    );
    expect(chip!.quality).toBe("poor");
    expect(chip!.label).toBe("实测偏弱");
  });
});
