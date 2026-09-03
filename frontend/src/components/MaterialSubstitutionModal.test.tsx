/**
 * Substitution modal.
 *
 * The thing worth guarding here is honesty about the prediction: without
 * RDKit the backend can only separate same-role materials on cost and VOC, and
 * an unchanged salt-spray figure rendered like a real result would tell the
 * user the swap is performance-neutral when nothing actually checked that.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { SubstitutionReport, SupplyRiskReport } from "../api";
import { useStore } from "../store";
import MaterialSubstitutionModal from "./MaterialSubstitutionModal";

const formulation = {
  name: "Primer A",
  domain: "anticorrosion_coating",
  ingredients: [
    { name: "Polyamide hardener", role: "hardener", weight_pct: 14 },
    { name: "Zinc phosphate", role: "inhibitor", weight_pct: 4 },
  ],
  predicted: {},
  warnings: [],
};

const report = (over: Partial<SubstitutionReport> = {}): SubstitutionReport =>
  ({
    original: "Polyamide hardener",
    slot_index: 1,
    role: "hardener",
    substitute_group: "epoxy_hardener",
    base_metrics: { cost_cny_per_kg: 18.6 },
    total_considered: 1,
    candidates: [
      {
        material: "Isophorone diamine (IPDA)",
        role: "hardener",
        functional_class: "cycloaliphatic_amine",
        substitute_group: "epoxy_hardener",
        availability: "in_stock",
        structural_score: 0.6667,
        structural_breakdown: { substitute_group: 1 },
        deltas: {
          cost_cny_per_kg: { before: 18.6, after: 23.2, delta: 4.6, pct: 24.9 },
          salt_spray_hours: { before: 800, after: 800, delta: 0, pct: 0 },
        },
        delta_confidence: "cost_only",
        feasible: true,
        blocking_reasons: [],
        score_after: 0.8,
      },
    ],
    ...over,
  }) as SubstitutionReport;

const noRisk: SupplyRiskReport = { at_risk: {}, affected: [] };

beforeEach(() => {
  vi.restoreAllMocks();
  useStore.setState({ leaderboard: [formulation] } as never);
});

describe("MaterialSubstitutionModal", () => {
  it("lists the current formulation's ingredients as swap targets", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    await waitFor(() => expect(api.supplyRisk).toHaveBeenCalled());
    expect(screen.getByRole("option", { name: /Polyamide hardener/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Zinc phosphate/ })).toBeInTheDocument();
  });

  it("shows the predicted deltas, not just a similarity score", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    vi.spyOn(api, "findSubstitutes").mockResolvedValue(report());

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /查找替代/ }));

    await waitFor(() =>
      expect(screen.getByText("Isophorone diamine (IPDA)")).toBeInTheDocument()
    );
    expect(screen.getByText("+24.9%")).toBeInTheDocument();
  });

  it("warns that performance deltas are not meaningful without descriptors", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    vi.spyOn(api, "findSubstitutes").mockResolvedValue(report());

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /查找替代/ }));

    // A 0% salt-spray change here means "the model cannot tell", not
    // "the swap is performance-neutral" — the UI has to say which.
    await waitFor(() => expect(screen.getByText(/未装 RDKit/)).toBeInTheDocument());
  });

  it("surfaces a supply-risk banner when a material is discontinued", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue({
      at_risk: { "Zinc phosphate": "discontinued" },
      affected: [{ formulation: "Primer A", affected_slots: [], suggestions: {} }],
    } as SupplyRiskReport);

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    // Scoped to the banner: the name also appears in the ingredient picker.
    const banner = (await screen.findByText(/供应风险/)).parentElement!;
    expect(banner.textContent).toContain("Zinc phosphate");
    expect(banner.textContent).toContain("discontinued");
  });

  it("explains the empty case instead of rendering a bare table", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    vi.spyOn(api, "findSubstitutes").mockResolvedValue(
      report({ candidates: [], total_considered: 0 })
    );

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /查找替代/ }));
    await waitFor(() =>
      expect(screen.getByText(/没有同组或同角色的替代品/)).toBeInTheDocument()
    );
  });

  it("prompts for a formulation rather than failing when the leaderboard is empty", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    useStore.setState({ leaderboard: [] } as never);

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    expect(screen.getByText(/配方排行为空/)).toBeInTheDocument();
  });

  it("reports a failed lookup instead of silently showing nothing", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    vi.spyOn(api, "findSubstitutes").mockRejectedValue(new Error("backend down"));

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /查找替代/ }));
    await waitFor(() => expect(screen.getByText("backend down")).toBeInTheDocument());
  });

  it("still renders when the supply-risk probe fails", async () => {
    // It runs on mount; a failure there must not take the modal down.
    vi.spyOn(api, "supplyRisk").mockRejectedValue(new Error("nope"));
    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /查找替代/ })).toBeInTheDocument()
    );
  });
});
