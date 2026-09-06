import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import KgRelationPanel from "./KgRelationPanel";

describe("KgRelationPanel path finder", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "kgFeedbackReport").mockResolvedValue({
      measured_total: 0,
      measured_performance: 0,
      by_campaign: {},
      alert: null,
      recent_bias: [],
    } as never);
    vi.spyOn(api, "kgFeedbackStats").mockResolvedValue({
      measured_total: 0,
      measured_performance: 0,
      by_campaign: {},
    } as never);
    vi.spyOn(api, "kgResolve").mockResolvedValue({
      query: "epoxy",
      chemicals: [
        {
          id: "chem:a",
          canonical_name: "Epoxy resin",
          cas_no: null,
          formula: null,
          composition_status: "resolved",
          mention_count: 1,
        },
      ],
      trade_products: [],
      expanded_entity_ids: [],
      top_relations: [],
      mode: "semantic",
      trade_only: false,
      interpretation: "",
    } as never);
    vi.spyOn(api, "kgSubstitutes").mockResolvedValue({
      query_entity_id: "chem:a",
      query_entity_name: "Epoxy resin",
      substitutes: [],
    } as never);
    vi.spyOn(api, "kgContradictions").mockResolvedValue({
      entity_id: "chem:a",
      contradictions: [],
    } as never);
    vi.spyOn(api, "kgRelations").mockResolvedValue([]);
  });

  it("calls kgPath and renders not-found result", async () => {
    const pathSpy = vi.spyOn(api, "kgPath").mockResolvedValue({
      src_entity_id: "chem:a",
      dst_entity_id: "chem:b",
      found: false,
      hops: 0,
      steps: [],
    });
    const user = userEvent.setup();
    render(<KgRelationPanel query="epoxy" />);

    await waitFor(() => expect(screen.getByText(/知识图谱关系/)).toBeTruthy());
    await user.click(screen.getByText(/知识图谱关系/));

    expect(await screen.findByTestId("kg-path-panel")).toBeTruthy();
    await user.type(screen.getByTestId("kg-path-src"), "chem:a");
    await user.type(screen.getByTestId("kg-path-dst"), "chem:b");
    await user.click(screen.getByTestId("kg-path-btn"));

    await waitFor(() => expect(pathSpy).toHaveBeenCalledWith("chem:a", "chem:b", 4));
    expect(await screen.findByTestId("kg-path-result")).toHaveTextContent(/未找到路径/);
  });
});
