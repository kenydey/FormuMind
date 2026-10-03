/**
 * The materials export buttons used ``window.open(url)`` — a navigation that cannot carry
 * the bearer token, so with API auth on the export opened a blank tab with a 401. They go
 * through the authenticated download helper now, and a failure is shown in the panel.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import MaterialsPanel from "./MaterialsPanel";

const calls: Array<[string, string | undefined]> = [];
let behaviour: () => Promise<string> = async () => "x";

vi.mock("../utils/download", () => ({
  downloadWithAuth: (url: string, name?: string) => {
    calls.push([url, name]);
    return behaviour();
  },
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listMaterials: vi.fn(async () => ({ materials: [] })),
      listMaterialCandidates: vi.fn(async () => ({ candidates: [] })),
      exportMaterialsUrl: vi.fn((fmt: string) => `/api/materials/export?format=${fmt}`),
    },
  };
});

vi.mock("../store", () => ({
  useStore: Object.assign(() => ({}), { getState: () => ({}), setState: () => undefined }),
}));

describe("MaterialsPanel export", () => {
  let openSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    calls.length = 0;
    behaviour = async () => "x";
    openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("downloads with the bearer token instead of opening a bare tab", async () => {
    render(<MaterialsPanel open onClose={() => undefined} />);
    fireEvent.click(await screen.findByTestId("materials-export-csv"));
    await waitFor(() => expect(calls).toEqual([["/api/materials/export?format=csv", "materials.csv"]]));
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("shows why an export failed", async () => {
    behaviour = async () => {
      throw new Error("Unauthorized");
    };
    render(<MaterialsPanel open onClose={() => undefined} />);
    fireEvent.click(await screen.findByTestId("materials-export-xlsx"));
    expect(await screen.findByText(/Unauthorized/)).toBeTruthy();
  });
});
