import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import HubLiteratureLibraryPane from "./HubLiteratureLibraryPane";

const getEnvFlags = vi.fn();
const getLiteratureLibrary = vi.fn();
const openSettings = vi.fn();
const patchCollection = vi.fn();
const deleteCollection = vi.fn();

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
    patchLiteratureCollection: (...a: unknown[]) => patchCollection(...a),
    deleteLiteratureCollection: (...a: unknown[]) => deleteCollection(...a),
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
          collection_ids: ["c1"],
        },
      ],
      collections: [{ id: "c1", name: "Coatings", item_ids: ["a"] }],
      frozen: null,
      coverage: { candidate_count: 1, frozen_count: 0 },
    });
    render(<HubLiteratureLibraryPane active />);
    await waitFor(() => {
      expect(screen.getByTestId("hub-library-item-a")).toBeInTheDocument();
    });
    expect(screen.getByText(/Epoxy paper/)).toBeInTheDocument();
    expect(getLiteratureLibrary).toHaveBeenCalled();
    const collBox = screen.getByTestId("hub-library-collections");
    expect(collBox).toBeInTheDocument();
    expect(collBox.textContent).toMatch(/Coatings/);
    expect(screen.getByTestId("hub-library-export-scope")).toBeInTheDocument();
  });

  describe("collection rename / delete", () => {
    const library = {
      project_id: "p1",
      items: [
        { id: "a", title: "Epoxy paper", doi: "10.1/a", screening: "unset", tags: [], authors: [], collection_ids: ["c1"] },
      ],
      collections: [{ id: "c1", name: "Coatings", item_ids: ["a"] }],
      frozen: null,
      coverage: { candidate_count: 1, frozen_count: 0 },
    };

    afterEach(() => {
      vi.restoreAllMocks(); // window.prompt / confirm spies must not leak between tests
    });

    beforeEach(() => {
      patchCollection.mockReset();
      deleteCollection.mockReset();
      patchCollection.mockResolvedValue({});
      deleteCollection.mockResolvedValue({});
      getEnvFlags.mockResolvedValue({ flags: [{ attr: "literature_library_enabled", value: true }] });
      getLiteratureLibrary.mockResolvedValue(library);
    });

    async function openWithCollectionSelected() {
      render(<HubLiteratureLibraryPane active />);
      await waitFor(() => screen.getByTestId("hub-library-item-a"));
      expect(screen.queryByTestId("hub-library-collection-rename")).toBeNull();
      expect(screen.queryByTestId("hub-library-collection-delete")).toBeNull();
      fireEvent.change(screen.getByTestId("hub-library-collection-filter"), { target: { value: "c1" } });
      await waitFor(() => screen.getByTestId("hub-library-collection-rename"));
    }

    it("renames the selected collection and reloads", async () => {
      vi.spyOn(window, "prompt").mockReturnValue("  防腐涂层  ");
      await openWithCollectionSelected();
      const before = getLiteratureLibrary.mock.calls.length;

      fireEvent.click(screen.getByTestId("hub-library-collection-rename"));

      await waitFor(() => expect(patchCollection).toHaveBeenCalledTimes(1));
      expect(patchCollection).toHaveBeenCalledWith("c1", { project_id: "p1", name: "防腐涂层" });
      await waitFor(() => expect(getLiteratureLibrary.mock.calls.length).toBeGreaterThan(before));
    });

    it("does not call the API when the name is unchanged or empty", async () => {
      const prompt = vi.spyOn(window, "prompt");
      await openWithCollectionSelected();

      prompt.mockReturnValueOnce("Coatings");
      fireEvent.click(screen.getByTestId("hub-library-collection-rename"));
      await waitFor(() => expect(prompt).toHaveBeenCalledTimes(1));
      prompt.mockReturnValueOnce("   ");
      fireEvent.click(screen.getByTestId("hub-library-collection-rename"));
      await waitFor(() => expect(prompt).toHaveBeenCalledTimes(2));
      expect(patchCollection).not.toHaveBeenCalled();
    });

    it("deletes only after confirmation and clears the collection filter", async () => {
      const confirm = vi.spyOn(window, "confirm");
      await openWithCollectionSelected();

      confirm.mockReturnValueOnce(false);
      fireEvent.click(screen.getByTestId("hub-library-collection-delete"));
      await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
      expect(deleteCollection).not.toHaveBeenCalled();

      confirm.mockReturnValueOnce(true);
      fireEvent.click(screen.getByTestId("hub-library-collection-delete"));
      await waitFor(() => expect(deleteCollection).toHaveBeenCalledWith("c1", { project_id: "p1" }));
      await waitFor(() =>
        expect((screen.getByTestId("hub-library-collection-filter") as HTMLSelectElement).value).toBe(""),
      );
      expect(screen.queryByTestId("hub-library-collection-delete")).toBeNull();
    });
  });
});
