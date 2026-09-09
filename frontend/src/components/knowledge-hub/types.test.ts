import { describe, expect, it } from "vitest";
import { resolveOpenUrl, type HubMaterialRow } from "./types";

describe("resolveOpenUrl", () => {
  it("prefers oa_pdf_url then url", () => {
    const row: HubMaterialRow = {
      row_key: "x",
      kind: "session",
      title: "t",
      source: "literature",
      identifier: "doi:1",
      url: "https://example.com/land",
      oa_pdf_url: "https://example.com/oa.pdf",
    };
    expect(resolveOpenUrl(row)).toBe("https://example.com/oa.pdf");
  });

  it("returns null when no http url", () => {
    const row: HubMaterialRow = {
      row_key: "x",
      kind: "kb",
      title: "t",
      source: "upload",
      identifier: "local-1",
    };
    expect(resolveOpenUrl(row)).toBeNull();
  });
});
