import { describe, expect, it } from "vitest";
import { DEFAULT_SEARCH_HINT, resolveSearchHint } from "./domainSearchHints";

describe("resolveSearchHint", () => {
  it("maps domain mode without hardcoding silane", () => {
    expect(resolveSearchHint({ search_hint_mode: "domain" }, "degreaser")).toBe(
      "脱脂剂 表面活性剂 清洗",
    );
    expect(resolveSearchHint({ search_hint_mode: "domain" }, "anticorrosion_coating")).toBe(
      "防腐涂料 底漆 缓蚀",
    );
    expect(resolveSearchHint({ search_hint_mode: "domain" }, null)).toBe(DEFAULT_SEARCH_HINT);
  });

  it("appends 综述 for literature mode", () => {
    expect(
      resolveSearchHint({ search_hint_mode: "domain_literature" }, "surface_treatment"),
    ).toBe("表面处理 转化膜 附着力 综述");
  });

  it("keeps explicit search_hint when mode absent", () => {
    expect(resolveSearchHint({ search_hint: "自定义 关键词" }, "degreaser")).toBe("自定义 关键词");
  });
});
