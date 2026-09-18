import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import FormulaLeaderboard from "./FormulaLeaderboard";

const proposeMaterialsMany = vi.fn();

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      proposeMaterialsMany: (...args: unknown[]) => proposeMaterialsMany(...args),
    },
  };
});

describe("FormulaLeaderboard validate banner actions", () => {
  beforeEach(() => {
    proposeMaterialsMany.mockReset();
    proposeMaterialsMany.mockResolvedValue({ upsert: 1, pending: 2, exists: 0 });
    useStore.setState({
      leaderboard: [
        {
          name: "Test Formula",
          domain: "coating",
          score: 0.8,
          ingredients: [
            {
              name: "Mystery Resin",
              role: "resin",
              weight_pct: 100,
              cas_no: null,
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
          warnings: [],
        },
      ],
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

  it("proposes missing ingredients from banner", async () => {
    render(<FormulaLeaderboard />);
    fireEvent.click(screen.getByTestId("validate-banner-propose"));
    await waitFor(() => {
      expect(proposeMaterialsMany).toHaveBeenCalled();
    });
    expect(screen.getByTestId("validate-banner-sync-msg")).toHaveTextContent("自动 1");
  });
});
