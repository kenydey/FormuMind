import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import FormulaLeaderboard from "./FormulaLeaderboard";

const proposeMaterialsMany = vi.fn();
const validateFormulations = vi.fn();
const scheduleAutosave = vi.fn();

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      proposeMaterialsMany: (...args: unknown[]) => proposeMaterialsMany(...args),
      validateFormulations: (...args: unknown[]) => validateFormulations(...args),
    },
  };
});

const baseFormula = {
  name: "Test Formula",
  domain: "coating" as const,
  score: 0.8,
  ingredients: [
    {
      name: "Mystery Resin",
      role: "resin",
      weight_pct: 100,
      cas_no: null as string | null,
      zh_name: null,
      smiles: null,
      formula: null,
      molar_mass: null,
      component_type: "resin",
    },
  ],
  predicted: { salt_spray_hours: 720, cost_cny_per_kg: 10 },
  predicted_std: {},
  rationale: "",
  warnings: [] as string[],
};

describe("FormulaLeaderboard validate banner actions", () => {
  beforeEach(() => {
    proposeMaterialsMany.mockReset();
    validateFormulations.mockReset();
    scheduleAutosave.mockReset();
    proposeMaterialsMany.mockResolvedValue({ upsert: 1, pending: 2, exists: 0 });
    validateFormulations.mockResolvedValue({
      formulations: [
        {
          ...baseFormula,
          ingredients: [
            {
              ...baseFormula.ingredients[0],
              cas_no: "25068-38-6",
              name: "Mystery Resin",
            },
          ],
        },
      ],
      // Catalog gap cleared; compliance remains.
      warnings: ["Mystery Resin: REACH SVHC 候选，商业化前需确认合规状态"],
    });
    useStore.setState({
      leaderboard: [baseFormula],
      formulationValidateWarnings: [
        "Test Formula: no CAS numbers resolved for ingredients",
        "Mystery Resin: REACH SVHC 候选，商业化前需确认合规状态",
      ],
      formulationBusy: false,
      requirement: {
        domain: "coating",
        objectives: [{ metric: "salt_spray_hours", direction: "maximize", weight: 1 }],
        materials: [],
        constraints: {},
      },
      research: null,
      openModal: null,
      error: null,
      scheduleAutosave,
    } as never);
  });

  it("shows propose + open-materials actions for catalog/compliance warnings", () => {
    render(<FormulaLeaderboard />);
    expect(screen.getByTestId("formula-validate-banner")).toBeInTheDocument();
    expect(screen.getByTestId("validate-banner-propose")).toBeInTheDocument();
    expect(screen.getByTestId("validate-banner-open-materials")).toBeInTheDocument();
    expect(screen.getByText(/合规项/)).toBeInTheDocument();
  });

  it("opens materials modal from banner", () => {
    render(<FormulaLeaderboard />);
    fireEvent.click(screen.getByTestId("validate-banner-open-materials"));
    expect(useStore.getState().openModal).toBe("materials");
  });

  it("proposes then re-validates and refreshes warnings", async () => {
    render(<FormulaLeaderboard />);
    fireEvent.click(screen.getByTestId("validate-banner-propose"));
    await waitFor(() => {
      expect(proposeMaterialsMany).toHaveBeenCalled();
      expect(validateFormulations).toHaveBeenCalled();
    });
    expect(screen.getByTestId("validate-banner-sync-msg")).toHaveTextContent("重校验 2→1");
    expect(useStore.getState().formulationValidateWarnings).toEqual([
      "Mystery Resin: REACH SVHC 候选，商业化前需确认合规状态",
    ]);
    expect(scheduleAutosave).toHaveBeenCalled();
  });

  it("keeps old warnings when re-validate fails", async () => {
    validateFormulations.mockRejectedValueOnce(new Error("validate down"));
    render(<FormulaLeaderboard />);
    fireEvent.click(screen.getByTestId("validate-banner-propose"));
    await waitFor(() => {
      expect(validateFormulations).toHaveBeenCalled();
    });
    expect(screen.getByTestId("validate-banner-sync-msg")).toHaveTextContent("重校验失败");
    expect(useStore.getState().formulationValidateWarnings).toHaveLength(2);
  });

  it("does not re-validate when propose fails", async () => {
    proposeMaterialsMany.mockRejectedValueOnce(new Error("propose down"));
    render(<FormulaLeaderboard />);
    fireEvent.click(screen.getByTestId("validate-banner-propose"));
    await waitFor(() => {
      expect(proposeMaterialsMany).toHaveBeenCalled();
    });
    expect(validateFormulations).not.toHaveBeenCalled();
    expect(screen.getByTestId("validate-banner-sync-msg")).toHaveTextContent("入库失败");
  });
});
