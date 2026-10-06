import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const designSelect = () => document.getElementById("doe-design") as HTMLSelectElement;
const generate = () => screen.getByRole("button", { name: /生成 DOE/ });

describe("DoeResultsPanel CCD star-point choice", () => {
  const generateDoe = vi.fn(async () => {});

  beforeEach(() => {
    generateDoe.mockClear();
    useStore.setState({
      doeEngine: "auto",
      alEngine: "auto",
      doePlan: null,
      models: [],
      modelHistory: {},
      busy: "idle",
      error: null,
      pendingDoeDesign: null,
      generateDoe,
      requirement: { domain: "anticorrosion_coating", substrate: "carbon_steel", objectives: [] },
    } as never);
    vi.mocked(api.getMeta).mockResolvedValue({
      domains: [],
      substrates: [],
      designs: [],
      example_projects: [],
      engines: { baybe: { available: false, label: "BayBE" }, pydoe: { available: true, label: "pyDOE" } },
    });
  });

  it("defaults to the central composite design with face-centred star points", async () => {
    render(<DoeResultsPanel />);
    expect(designSelect().value).toBe("ccd");
    const alpha = (await screen.findByTestId("doe-ccd-alpha-select")) as HTMLSelectElement;
    expect(alpha.value).toBe("face");
    fireEvent.click(generate());
    expect(generateDoe).toHaveBeenCalledWith("ccd", { ccdAlpha: "face" });
  });

  it("sends the rotatable choice", async () => {
    render(<DoeResultsPanel />);
    fireEvent.change(await screen.findByTestId("doe-ccd-alpha-select"), { target: { value: "rotatable" } });
    fireEvent.click(generate());
    expect(generateDoe).toHaveBeenCalledWith("ccd", { ccdAlpha: "rotatable" });
  });

  it("offers the inscribed design: rotatable and still inside the ranges", async () => {
    render(<DoeResultsPanel />);
    const alpha = (await screen.findByTestId("doe-ccd-alpha-select")) as HTMLSelectElement;
    expect(Array.from(alpha.options).map((o) => o.value)).toEqual(["face", "inscribed", "rotatable"]);
    fireEvent.change(alpha, { target: { value: "inscribed" } });
    fireEvent.click(generate());
    expect(generateDoe).toHaveBeenCalledWith("ccd", { ccdAlpha: "inscribed" });
  });

  it("offers the choice for the central composite design only", async () => {
    render(<DoeResultsPanel />);
    fireEvent.change(designSelect(), { target: { value: "lhs" } });
    expect(screen.queryByTestId("doe-ccd-alpha-select")).toBeNull();
    fireEvent.click(generate());
    expect(generateDoe).toHaveBeenCalledWith("lhs", undefined);
  });

  it("sends the design the select shows when the engine took the chosen one away", async () => {
    render(<DoeResultsPanel />);
    fireEvent.change(designSelect(), { target: { value: "full_factorial" } });
    fireEvent.change(screen.getByTestId("doe-engine-select"), { target: { value: "pydoe" } });
    // pyDOE offers no full factorial: the select falls back to its first entry, and that is what gets generated
    await waitFor(() => expect(designSelect().value).toBe("ccd"));
    fireEvent.click(generate());
    expect(generateDoe).toHaveBeenCalledWith("ccd", { ccdAlpha: "face" });
  });

  it("follows a design chosen elsewhere (the agent's suggestion)", async () => {
    render(<DoeResultsPanel />);
    useStore.setState({ pendingDoeDesign: "lhs" } as never);
    await waitFor(() => expect(designSelect().value).toBe("lhs"));
  });
});
