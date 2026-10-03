/**
 * Switching the active project while the Literature Library pane is open:
 *  - a slow response for the previous project must not overwrite the new project's list;
 *  - the filters (collection / query / tag / screening) belong to the previous project and
 *    must not follow it — a collection id of project A sent for project B just yields an
 *    empty library.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import HubLiteratureLibraryPane from "./HubLiteratureLibraryPane";

const getEnvFlags = vi.fn();
const getLiteratureLibrary = vi.fn();

vi.mock("../../api", () => ({
  formatApiError: (e: unknown) => (e instanceof Error ? e.message : String(e)),
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
    patchLiteratureCollection: vi.fn(),
    deleteLiteratureCollection: vi.fn(),
  },
}));

function lib(projectId: string, title: string) {
  return {
    project_id: projectId,
    items: [
      { id: `${projectId}-1`, title, doi: "", screening: "unset", tags: [], authors: [], collection_ids: ["c1"] },
    ],
    collections: [{ id: "c1", name: "Coatings", item_ids: [`${projectId}-1`] }],
    frozen: null,
    coverage: { candidate_count: 1, frozen_count: 0 },
  };
}

beforeEach(() => {
  getEnvFlags.mockReset();
  getLiteratureLibrary.mockReset();
  getEnvFlags.mockResolvedValue({ flags: [{ attr: "literature_library_enabled", value: true }] });
  useStore.setState({ activeProjectId: "pA", envFlagsRevision: 0, openSettings: vi.fn() } as never);
});

describe("HubLiteratureLibraryPane project switch", () => {
  it("ignores a late response for the project that was switched away from", async () => {
    let releaseA!: (v: unknown) => void;
    getLiteratureLibrary.mockImplementation((projectId: string) =>
      projectId === "pA"
        ? new Promise((res) => {
            releaseA = res;
          })
        : Promise.resolve(lib("pB", "Paper of project B")),
    );
    render(<HubLiteratureLibraryPane active />);
    await waitFor(() => expect(getLiteratureLibrary).toHaveBeenCalledWith("pA", expect.anything()));

    act(() => {
      useStore.setState({ activeProjectId: "pB" } as never);
    });
    await screen.findByText(/Paper of project B/);

    await act(async () => {
      releaseA(lib("pA", "Paper of project A"));
    });
    expect(screen.queryByText(/Paper of project A/)).toBeNull();
    expect(screen.getByText(/Paper of project B/)).toBeTruthy();
  });

  it("does not carry the previous project's collection filter over", async () => {
    getLiteratureLibrary.mockImplementation((projectId: string) =>
      Promise.resolve(lib(projectId, `Paper of ${projectId}`)),
    );
    render(<HubLiteratureLibraryPane active />);
    await screen.findByText(/Paper of pA/);
    fireEvent.change(screen.getByTestId("hub-library-collection-filter"), { target: { value: "c1" } });
    await waitFor(() =>
      expect(getLiteratureLibrary).toHaveBeenCalledWith("pA", expect.objectContaining({ collection_id: "c1" })),
    );

    getLiteratureLibrary.mockClear();
    act(() => {
      useStore.setState({ activeProjectId: "pB" } as never);
    });
    await screen.findByText(/Paper of pB/);

    for (const call of getLiteratureLibrary.mock.calls) {
      expect(call[0]).toBe("pB");
      expect((call[1] as { collection_id?: string }).collection_id).toBeUndefined();
    }
    expect((screen.getByTestId("hub-library-collection-filter") as HTMLSelectElement).value).toBe("");
  });
});
