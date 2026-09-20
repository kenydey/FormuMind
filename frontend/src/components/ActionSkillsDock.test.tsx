import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ActionSkillsDock from "./ActionSkillsDock";
import { useStore } from "../store";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listFormulationSkills: vi.fn(async () => [
        {
          id: "silane_recommend",
          title: "硅烷偶联推荐",
          summary: "demo skill",
          when_to_use: "when needed",
          action: "recommend",
          modal: "recommend",
          icon: "⭐",
          tools: ["kb_hybrid", "crag_grade"],
          checklist: [
            { id: "retrieve", title: "检索知识库" },
            { id: "grade", title: "CRAG 评估" },
          ],
          presets: { prefer_materials_catalog: true, search_hint: "硅烷" },
        },
      ]),
    },
  };
});

describe("ActionSkillsDock", () => {
  beforeEach(() => {
    useStore.setState({
      activeSkillId: null,
      activeSkill: null,
      pendingDoeDesign: null,
      openModal: null,
      preferMaterialsCatalog: false,
      searchQuery: "",
      taskThinking: [],
      formulationBusy: false,
      deepResearchBusy: false,
      busy: "idle",
    });
  });

  it("loads skills and applyFormulationSkill opens recommend modal", async () => {
    const user = userEvent.setup();
    render(<ActionSkillsDock />);
    await waitFor(() => expect(screen.getByTestId("skill-chip-silane_recommend")).toBeInTheDocument());
    await user.click(screen.getByTestId("skill-chip-silane_recommend"));
    const s = useStore.getState();
    expect(s.activeSkillId).toBe("silane_recommend");
    expect(s.openModal).toBe("recommend");
    expect(s.preferMaterialsCatalog).toBe(true);
    expect(s.searchQuery).toBe("硅烷");
    expect(screen.getByTestId("active-skill-panel")).toBeInTheDocument();
    expect(screen.getByTestId("skill-check-retrieve")).toBeInTheDocument();
  });
});
