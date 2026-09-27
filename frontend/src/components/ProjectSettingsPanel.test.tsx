import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useStore } from "../store";
import ProjectSettingsPanel from "./ProjectSettingsPanel";

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
  // 注意: 不用 fake timers —— userEvent 需要真实时钟;
  // scheduleAutosave 的防抖 timer 在测试时长内不会触发, 不影响断言。
}

describe("ProjectSettingsPanel (W3-6)", () => {
  it("renders multiline textarea with current agent_context", () => {
    seedStore();
    render(<ProjectSettingsPanel />);
    const ta = screen.getByTestId("agent-context-textarea");
    expect(ta.tagName).toBe("TEXTAREA");
    expect(ta).toHaveValue("旧上下文");
  });

  it("editing marks dirty and save writes to store", async () => {
    seedStore();
    const user = userEvent.setup({ delay: null });
    render(<ProjectSettingsPanel />);

    const ta = screen.getByTestId("agent-context-textarea");
    await user.clear(ta);
    await user.type(ta, "新的多行\n上下文内容");
    expect(screen.getByTestId("agent-context-dirty")).toBeInTheDocument();

    await user.click(screen.getByTestId("agent-context-save"));
    expect(useStore.getState().agentContext).toBe("新的多行\n上下文内容");
    expect(screen.getByTestId("agent-context-saved")).toBeInTheDocument();
  });

  it("shows hint when no project is open", () => {
    useStore.setState({ activeProjectId: null } as never);
    render(<ProjectSettingsPanel />);
    expect(screen.getByTestId("project-settings-panel")).toHaveTextContent(
      "请先打开或新建项目"
    );
  });
});
