import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useStore } from "../store";
import ProjectAgentContext from "./ProjectAgentContext";

function seedStore() {
  useStore.setState({
    activeProjectId: "proj-1",
    projects: [
      {
        id: "proj-1",
        title: "测试项目",
        headline: "",
        domain: "",
        created_at: "",
        updated_at: "",
        source_count: 0,
        chat_count: 0,
        leaderboard_count: 0,
        has_doe: false,
        has_optimize: false,
        has_loop: false,
      },
    ],
    agentContext: "旧上下文",
    projectSaveBusy: false,
  } as never);
}

describe("ProjectAgentContext (Wave 0: moved next to project switcher)", () => {
  it("renders nothing when no project is open", () => {
    useStore.setState({ activeProjectId: null } as never);
    const { container } = render(<ProjectAgentContext />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows collapsed toggle with current context preview", () => {
    seedStore();
    render(<ProjectAgentContext />);
    expect(screen.getByTestId("project-agent-context-toggle")).toHaveTextContent("旧上下文");
    expect(screen.queryByTestId("agent-context-textarea")).not.toBeInTheDocument();
  });

  it("expands, edits and saves", async () => {
    seedStore();
    const user = userEvent.setup({ delay: null });
    render(<ProjectAgentContext />);

    await user.click(screen.getByTestId("project-agent-context-toggle"));
    const ta = screen.getByTestId("agent-context-textarea");
    expect(ta).toHaveValue("旧上下文");

    await user.clear(ta);
    await user.type(ta, "新的多行\n上下文内容");
    expect(screen.getByTestId("agent-context-dirty")).toBeInTheDocument();

    await user.click(screen.getByTestId("agent-context-save"));
    expect(useStore.getState().agentContext).toBe("新的多行\n上下文内容");
    expect(screen.getByTestId("agent-context-saved")).toBeInTheDocument();
  });
});
