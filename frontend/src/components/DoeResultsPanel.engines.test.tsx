import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import DoeResultsPanel from "./DoeResultsPanel";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getMeta: vi.fn(),
      suggestFactors: vi.fn().mockResolvedValue({ factors: [] }),
    },
  };
});

describe("DoeResultsPanel engine probes", () => {
  beforeEach(() => {
    useStore.setState({
      doeEngine: "auto",
      alEngine: "auto",
      doePlan: null,
      models: [],
      modelHistory: {},
      busy: "idle",
      error: null,
      requirement: {
        domain: "anticorrosion_coating",
        substrate: "carbon_steel",
        objectives: [],
      },
    } as never);
    vi.mocked(api.getMeta).mockResolvedValue({
      domains: [],
      substrates: [],
      designs: [],
      example_projects: [],
      engines: {
        baybe: { available: false, label: "BayBE" },
        pydoe: { available: false, label: "pyDOE" },
      },
    });
  });

  it("disables baybe and pydoe options when meta says unavailable", async () => {
    render(<DoeResultsPanel />);
    const al = await screen.findByTestId("al-engine-select");
    const doe = screen.getByTestId("doe-engine-select");
    await waitFor(() => {
      const baybeOpt = al.querySelector('option[value="baybe"]') as HTMLOptionElement;
      expect(baybeOpt.disabled).toBe(true);
      expect(baybeOpt.textContent).toMatch(/未安装/);
    });
    const pydoeOpt = doe.querySelector('option[value="pydoe"]') as HTMLOptionElement;
    expect(pydoeOpt.disabled).toBe(true);
    expect(pydoeOpt.textContent).toMatch(/未安装/);
  });
});
