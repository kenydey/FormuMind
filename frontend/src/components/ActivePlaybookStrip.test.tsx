import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import ActivePlaybookStrip from "./ActivePlaybookStrip";
import { useStore } from "../store";

describe("ActivePlaybookStrip", () => {
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

  it("renders nothing when no active playbook", () => {
    const { container } = render(<ActivePlaybookStrip />);
    expect(container.querySelector('[data-testid="active-playbook-strip"]')).toBeNull();
  });

  it("shows checklist and clear for active playbook", async () => {
    const user = userEvent.setup();
    useStore.setState({
      activeSkillId: "formula_recommend",
      activeSkill: {
        id: "formula_recommend",
        title: "配方推荐",
        summary: "泛化配方",
        when_to_use: "",
        action: "recommend",
        modal: "recommend",
        icon: "⭐",
        tools: ["kb_hybrid"],
        checklist: [
          { id: "retrieve", title: "检索知识库" },
          { id: "grade", title: "CRAG 评估" },
        ],
        presets: { prefer_materials_catalog: true, search_hint_mode: "domain" },
      },
      preferMaterialsCatalog: true,
    });
    render(<ActivePlaybookStrip />);
    expect(screen.getByTestId("active-playbook-strip")).toBeInTheDocument();
    expect(screen.getByText("配方推荐")).toBeInTheDocument();
    expect(screen.getByTestId("skill-check-retrieve")).toBeInTheDocument();
    expect(screen.getByText(/优先材料库/)).toBeInTheDocument();
    await user.click(screen.getByTestId("clear-skill"));
    expect(useStore.getState().activeSkillId).toBeNull();
  });
});
