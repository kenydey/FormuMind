import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ArtifactDrawer from "./ArtifactDrawer";
import { useStore } from "../store";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listProjectExports: vi.fn(async () => [
        {
          name: "leaderboard_1.json",
          size: 12,
          updated_at: "2026-09-15T12:00:00Z",
          content_type: "application/json",
        },
      ]),
      saveProjectExport: vi.fn(async () => ({
        name: "x.json",
        size: 1,
        updated_at: "2026-09-15T12:00:00Z",
        content_type: "application/json",
      })),
      deleteProjectExport: vi.fn(async () => ({ ok: true, filename: "x.json" })),
      downloadProjectExportUrl: (id: string, name: string) =>
        `/api/projects/${id}/exports/${name}`,
    },
  };
});

describe("ArtifactDrawer", () => {
  beforeEach(() => {
    useStore.setState({
      artifactDrawerOpen: true,
      activeArtifactId: null,
      historyOpen: false,
      openModal: null,
      activeProjectId: "proj-1",
      leaderboard: [
        {
          id: "f1",
          name: "demo",
          score: 0.9,
          ingredients: [],
          rationale: "",
          sources: [],
          warnings: [],
          predicted: {},
          predicted_std: {},
          domain: "anticorrosion_coating",
        } as never,
      ],
      formulationBusy: false,
      doePlan: null,
      busy: "idle",
      optimizationHistory: [],
      deepReport: null,
      deepResearchBusy: false,
      loopReport: null,
    });
  });

  it("lists live artifacts and openArtifact opens the matching modal", async () => {
    const user = userEvent.setup();
    render(<ArtifactDrawer />);
    expect(screen.getByTestId("artifact-drawer")).toBeInTheDocument();
    expect(screen.getByTestId("artifact-card-leaderboard")).toBeInTheDocument();
    await user.click(screen.getByTestId("artifact-card-leaderboard"));
    const s = useStore.getState();
    expect(s.activeArtifactId).toBe("leaderboard");
    expect(s.openModal).toBe("recommend");
    expect(s.artifactDrawerOpen).toBe(true);
  });

  it("shows empty state when there are no artifacts", () => {
    useStore.setState({ leaderboard: [] });
    render(<ArtifactDrawer />);
    expect(screen.getByText(/暂无产物/)).toBeInTheDocument();
  });

  it("shows export shelf files on shelf tab", async () => {
    const user = userEvent.setup();
    render(<ArtifactDrawer />);
    await user.click(screen.getByTestId("artifact-tab-shelf"));
    expect(await screen.findByTestId("export-shelf-panel")).toBeInTheDocument();
    expect(await screen.findByTestId("shelf-file-leaderboard_1.json")).toBeInTheDocument();
  });
});
