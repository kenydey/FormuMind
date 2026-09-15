import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import ArtifactDrawer from "./ArtifactDrawer";
import { useStore } from "../store";

describe("ArtifactDrawer", () => {
  beforeEach(() => {
    useStore.setState({
      artifactDrawerOpen: true,
      activeArtifactId: null,
      historyOpen: false,
      openModal: null,
      leaderboard: [
        {
          id: "f1",
          name: "demo",
          score: 0.9,
          ingredients: [],
          rationale: "",
          sources: [],
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
});
