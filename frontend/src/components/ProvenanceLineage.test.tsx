import { describe, expect, it, vi, type Mock } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { api } from "../api";
import ProvenanceLineage from "./ProvenanceLineage";

vi.mock("../api", () => ({
  api: { getProvenanceLineage: vi.fn() },
  formatApiError: (e: unknown) => (e instanceof Error ? e.message : String(e)),
}));

const getProvenanceLineage = api.getProvenanceLineage as Mock;

describe("ProvenanceLineage (W3-10)", () => {
  it("renders the upstream edge chain with node badges and relations", async () => {
    getProvenanceLineage.mockResolvedValue({
      node_type: "formulation",
      node_id: "f1",
      depth: 3,
      edges: [
        {
          from_type: "claim",
          from_id: "claim-abc123",
          to_type: "formulation",
          to_id: "f1",
          relation: "supports",
        },
        {
          from_type: "source",
          from_id: "src-xyz",
          to_type: "claim",
          to_id: "claim-abc123",
          relation: "cited_by",
        },
      ],
    });

    render(
      <ProvenanceLineage nodeType="formulation" nodeId="f1" title="测试配方" onClose={() => {}} />
    );
    await waitFor(() => expect(screen.getByTestId("prov-edge-0")).toBeTruthy());

    expect(getProvenanceLineage).toHaveBeenCalledWith("formulation", "f1");
    const edge0 = screen.getByTestId("prov-edge-0").textContent ?? "";
    expect(edge0).toContain("结论");
    expect(edge0).toContain("supports");
    expect(edge0).toContain("配方");
    const edge1 = screen.getByTestId("prov-edge-1").textContent ?? "";
    expect(edge1).toContain("文献");
    expect(edge1).toContain("cited_by");
  });

  it("shows the empty state when no edges exist", async () => {
    getProvenanceLineage.mockResolvedValue({
      node_type: "formulation",
      node_id: "f9",
      depth: 3,
      edges: [],
    });

    render(<ProvenanceLineage nodeType="formulation" nodeId="f9" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("prov-empty")).toBeTruthy());
    expect(screen.getByTestId("prov-empty").textContent).toContain("暂无谱系记录");
  });

  it("shows an error box when the API fails", async () => {
    getProvenanceLineage.mockRejectedValue(new Error("boom"));
    render(<ProvenanceLineage nodeType="formulation" nodeId="f1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("boom")).toBeTruthy());
  });
});
