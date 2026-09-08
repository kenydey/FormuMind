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
import { api, ApiError } from "../api";
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

  it("lists 404 candidates so the user can pick a real slot material", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    vi.spyOn(api, "findSubstitutes").mockRejectedValue(
      new ApiError("配方中不含材料：poly", {
        candidates: ["Polyamide hardener", "Zinc phosphate"],
      })
    );

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /查找替代/ }));
    await waitFor(() => expect(screen.getByText(/配方中不含材料/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Polyamide hardener" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zinc phosphate" })).toBeInTheDocument();
  });

  it("defaults to networked search and shows external candidates", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    const spy = vi.spyOn(api, "findSubstitutes").mockResolvedValue(
      report({
        identity: {
          query: "Polyamide hardener",
          cas_no: "",
          smiles: "CCN",
          source: "catalog",
          resolved: true,
        },
        external: [
          {
            name: "Fake Ext Amine",
            cas_no: "111-11-1",
            smiles: "CCN",
            cid: 1,
            similarity: 0.91,
            source: "pubchem_similar",
            in_catalog: false,
            note: "结构相似",
          },
        ],
        external_meta: {
          enabled: true,
          queried: true,
          count: 1,
          skipped_reason: null,
          provider: "pubchem_fastsimilarity_2d",
        },
      })
    );

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    expect(screen.getByTestId("include-external-substitutes")).toBeInTheDocument();
    const checkbox = screen.getByTestId("include-external-substitutes").querySelector("input");
    expect(checkbox).toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: /查找替代/ }));
    await waitFor(() => expect(screen.getByText("Fake Ext Amine")).toBeInTheDocument());
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ include_external: true })
    );
    expect(screen.getByRole("button", { name: /入库并选用/ })).toBeInTheDocument();
  });

  it("defaults to literature search and shows literature candidates", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    const spy = vi.spyOn(api, "findSubstitutes").mockResolvedValue(
      report({
        layers_used: ["catalog", "literature"],
        literature: [
          {
            name: "Lit Substitute Amine",
            source: "kg",
            confidence: 0.72,
            entity_id: "chem:lit:amine",
            cas_no: null,
            smiles: null,
            role_hint: "hardener",
            in_catalog: false,
            evidence: [
              {
                source_id: "doi:10.1/x",
                sentence: "IPDA may replace polyamide hardeners in epoxy systems.",
                confidence: 0.7,
              },
            ],
            note: "知识图谱 substitutes 边",
          },
        ],
        literature_meta: {
          enabled: true,
          queried: true,
          count: 1,
          skipped_reason: null,
          providers: ["kg"],
        },
      })
    );

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    expect(screen.getByTestId("include-literature-substitutes")).toBeInTheDocument();
    const checkbox = screen.getByTestId("include-literature-substitutes").querySelector("input");
    expect(checkbox).toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: /查找替代/ }));
    await waitFor(() => expect(screen.getByText("Lit Substitute Amine")).toBeInTheDocument());
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ include_literature: true })
    );
    expect(screen.getByTestId("literature-substitutes-section")).toBeInTheDocument();
    expect(screen.getByTestId("substitute-layers-used").textContent).toContain("literature");
    expect(screen.getByRole("button", { name: /入库并选用/ })).toBeInTheDocument();
  });

  it("marks in-catalog literature rows as already available above", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    vi.spyOn(api, "findSubstitutes").mockResolvedValue(
      report({
        literature: [
          {
            name: "Isophorone diamine (IPDA)",
            source: "kb_product",
            confidence: 0.5,
            in_catalog: true,
            catalog_name: "Isophorone diamine (IPDA)",
            evidence: [],
          },
        ],
        literature_meta: {
          enabled: true,
          queried: true,
          count: 1,
          skipped_reason: null,
          providers: ["kb_product"],
        },
      })
    );

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /查找替代/ }));
    await waitFor(() => expect(screen.getByText(/已在库/)).toBeInTheDocument());
    expect(screen.getByText("见上方")).toBeInTheDocument();
  });

  it("defaults AI mode to auto and can show llm suggestions", async () => {
    vi.spyOn(api, "supplyRisk").mockResolvedValue(noRisk);
    const spy = vi.spyOn(api, "findSubstitutes").mockResolvedValue(
      report({
        candidates: [],
        total_considered: 0,
        layers_used: ["catalog", "llm"],
        llm: [
          {
            name: "Lanthanum nitrate",
            kind: "substitute_inhibitor",
            rationale: "rare-earth passivation salt",
            source: "llm_expand",
            in_catalog: false,
            note: "AI/规则建议；未做配方 Δ",
          },
        ],
        llm_meta: {
          enabled: true,
          queried: true,
          count: 1,
          skipped_reason: null,
          mode: "auto",
          providers: ["llm_expand"],
        },
      })
    );

    render(<MaterialSubstitutionModal onClose={vi.fn()} />);
    expect(screen.getByTestId("include-llm-mode")).toHaveValue("auto");
    await userEvent.click(screen.getByRole("button", { name: /查找替代/ }));
    await waitFor(() => expect(screen.getByText("Lanthanum nitrate")).toBeInTheDocument());
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ include_llm: null })
    );
    expect(screen.getByTestId("llm-substitutes-section")).toBeInTheDocument();
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
