import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import HubLiteratureLibraryPane from "./HubLiteratureLibraryPane";

const getEnvFlags = vi.fn();
const getLiteratureLibrary = vi.fn();
const openSettings = vi.fn();

vi.mock("../../api", () => ({
  api: {
    getEnvFlags: (...a: unknown[]) => getEnvFlags(...a),
    getLiteratureLibrary: (...a: unknown[]) => getLiteratureLibrary(...a),
    captureLiteratureManifest: vi.fn(),
    freezeLiteratureManifest: vi.fn(),
    enrichLiteratureOa: vi.fn(),
    importLiteratureIds: vi.fn(),
    importLiteratureCitation: vi.fn(),
    exportLiteratureBib: vi.fn(),
    exportLiteratureRis: vi.fn(),
    getLiteratureDuplicates: vi.fn(),
    mergeLiteratureItems: vi.fn(),
    patchLiteratureItem: vi.fn(),
    createLiteratureCollection: vi.fn(),
  },
}));

describe("HubLiteratureLibraryPane", () => {
  beforeEach(() => {
    getEnvFlags.mockReset();
    getLiteratureLibrary.mockReset();
    openSettings.mockReset();
    useStore.setState({
      activeProjectId: "p1",
      envFlagsRevision: 0,
      openSettings,
    } as never);
  });

  it("shows flag CTA when literature_library_enabled is off", async () => {
    getEnvFlags.mockResolvedValue({
      flags: [{ attr: "literature_library_enabled", value: false }],
    });
    render(<HubLiteratureLibraryPane active />);
    await waitFor(() => {
      expect(screen.getByTestId("hub-library-flag-cta")).toBeInTheDocument();
    });
    expect(getLiteratureLibrary).not.toHaveBeenCalled();
  });

  it("loads library when flag is on", async () => {
    getEnvFlags.mockResolvedValue({
      flags: [{ attr: "literature_library_enabled", value: true }],
    });
    getLiteratureLibrary.mockResolvedValue({
      project_id: "p1",
      items: [
        {
          id: "a",
          title: "Epoxy paper",
          doi: "10.1000/epoxy",
          screening: "unset",
          tags: [],
          authors: [],
        },
      ],
      collections: [],
      frozen: null,
      coverage: { candidate_count: 1, frozen_count: 0 },
    });
    render(<HubLiteratureLibraryPane active />);
    await waitFor(() => {
      expect(screen.getByTestId("hub-library-item-a")).toBeInTheDocument();
    });
    expect(screen.getByText(/Epoxy paper/)).toBeInTheDocument();
    expect(getLiteratureLibrary).toHaveBeenCalled();
  });
});
