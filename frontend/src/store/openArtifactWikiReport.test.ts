import { describe, expect, it, beforeEach } from "vitest";
import { useStore } from "../store";

describe("openArtifact wiki_report", () => {
  beforeEach(() => {
    useStore.setState({
      openModal: null,
      knowledgeHubTab: "materials",
      artifactDrawerOpen: false,
      activeArtifactId: null,
      historyOpen: false,
    } as never);
  });

  it("opens Knowledge Hub on reports tab", () => {
    useStore.getState().openArtifact("wiki_report");
    const s = useStore.getState();
    expect(s.openModal).toBe("knowledge");
    expect(s.knowledgeHubTab).toBe("reports");
    expect(s.artifactDrawerOpen).toBe(true);
    expect(s.activeArtifactId).toBe("wiki_report");
  });
});
