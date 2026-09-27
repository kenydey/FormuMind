import { describe, expect, it, vi, type Mock } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { api } from "../api";
import TableBadges from "./TableBadges";

vi.mock("../api", () => ({
  api: { getSourceTables: vi.fn() },
  formatApiError: (e: unknown) => (e instanceof Error ? e.message : String(e)),
}));

const getSourceTables = api.getSourceTables as Mock;

describe("TableBadges (W3-7)", () => {
  it("renders kind badges, caption, page and row preview", async () => {
    getSourceTables.mockResolvedValue({
      source_id: "s1",
      tables: [
        {
          table_id: "s1#p01-00",
          page_no: 1,
          caption: "表1 配方组成",
          kind: "recipe",
          headers: ["组分", "质量份"],
          rows: [
            ["环氧树脂", 100],
            ["固化剂", 25],
            ["助剂", 2],
            ["颜料", 10],
          ],
        },
        {
          table_id: "s1#p02-00",
          page_no: 2,
          caption: "Table 2 Salt spray",
          kind: "performance",
          headers: ["项目", "结果"],
          rows: [["盐雾", "1000h"]],
        },
      ],
    });

    render(<TableBadges sourceId="s1" />);
    await waitFor(() => expect(screen.getByTestId("table-badges")).toBeTruthy());

    expect(screen.getByTestId("table-kind-0").textContent).toContain("配方表");
    expect(screen.getByTestId("table-kind-1").textContent).toContain("性能表");
    expect(screen.getByText("表1 配方组成")).toBeTruthy();
    expect(screen.getByText("环氧树脂")).toBeTruthy();
    // only first 3 rows previewed
    expect(screen.queryByText("颜料")).toBeNull();
    expect(screen.getByText(/共 4 行/)).toBeTruthy();
  });

  it("renders PropertySet properties and warnings when present (W3-1 contract)", async () => {
    getSourceTables.mockResolvedValue({
      source_id: "s1",
      tables: [
        {
          table_id: "s1#p03-00",
          kind: "tds_sds",
          headers: [],
          rows: [],
          property_set: {
            table_id: "s1#p03-00",
            properties: [
              {
                name: "黏度",
                name_normalized: "viscosity",
                value: 2500,
                unit: "mPa·s",
                unit_normalized: "mPa.s",
                raw_text: "黏度 2500 mPa·s",
              },
              { name: "未知属性X", value: null, raw_text: "未知属性X --" },
            ],
            warnings: ["unmapped: 未知属性X"],
          },
        },
      ],
    });

    render(<TableBadges sourceId="s1" />);
    await waitFor(() => expect(screen.getByTestId("table-props-0")).toBeTruthy());
    expect(screen.getByTestId("table-kind-0").textContent).toContain("TDS/SDS");
    expect(screen.getByText("viscosity: 2500 mPa.s")).toBeTruthy();
    expect(screen.getByText("未知属性X: 未知属性X --")).toBeTruthy();
    expect(screen.getByText(/unmapped/)).toBeTruthy();
  });

  it("renders nothing when no tables; degrades on missing fields", async () => {
    getSourceTables.mockResolvedValue({ source_id: "s1", tables: [] });
    const { container } = render(<TableBadges sourceId="s1" />);
    await waitFor(() => expect(getSourceTables).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector("[data-testid='table-badges']")).toBeNull());

    // missing kind/headers/rows must not crash
    getSourceTables.mockResolvedValue({
      source_id: "s2",
      tables: [{ table_id: "x" }],
    });
    render(<TableBadges sourceId="s2" />);
    await waitFor(() => expect(screen.getByTestId("table-badges")).toBeTruthy());
    expect(screen.getByTestId("table-kind-0").textContent).toContain("表格");
  });
});
