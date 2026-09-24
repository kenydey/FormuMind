/**
 * MaterialsPanel supplier editor (W2′): add / edit / remove rows before save.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import MaterialsPanel from "./MaterialsPanel";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listMaterials: vi.fn(async () => ({ materials: [] })),
      listMaterialCandidates: vi.fn(async () => ({ candidates: [] })),
      upsertMaterial: vi.fn(async () => ({ ok: true })),
      exportMaterialsUrl: vi.fn(() => "/export"),
      chemicalLookup: vi.fn(),
      setMaterialAvailability: vi.fn(),
      enrichMaterials: vi.fn(),
      importMaterials: vi.fn(),
    },
  };
});

vi.mock("../store", () => ({
  useStore: Object.assign(() => ({}), { getState: () => ({}), setState: () => undefined }),
}));

describe("MaterialsPanel suppliers editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lets the user add and fill a supplier row on create", async () => {
    render(<MaterialsPanel open onClose={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "+ 新材料" })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "+ 新材料" }));

    expect(await screen.findByTestId("materials-suppliers-editor")).toBeTruthy();
    fireEvent.click(screen.getByTestId("materials-suppliers-add"));

    const name = await screen.findByTestId("materials-supplier-name-0");
    fireEvent.change(name, { target: { value: "Acme Chem" } });
    fireEvent.change(screen.getByTestId("materials-supplier-url-0"), {
      target: { value: "https://acme.example" },
    });
    expect((name as HTMLInputElement).value).toBe("Acme Chem");

    fireEvent.click(screen.getByTestId("materials-supplier-remove-0"));
    expect(screen.queryByTestId("materials-supplier-name-0")).toBeNull();
  });
});
