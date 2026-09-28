import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import KnowledgeHubModal from "./KnowledgeHubModal";

vi.mock("../WikiMarkdownReader", () => ({
  default: () => null,
}));

describe("KnowledgeHubModal reports tab", () => {
  beforeEach(() => {
    useStore.setState({
      knowledgeHubTab: "reports",
      activeProjectId: "proj-demo",
    } as never);
  });

  it("labels Reports as live dossier-backed generation, not 预留", () => {
    render(<KnowledgeHubModal open onClose={() => undefined} />);
    expect(screen.getByTestId("modal-knowledge-hub")).toBeInTheDocument();
    const reportsTab = screen.getByTestId("hub-tab-reports");
    expect(reportsTab.textContent).toMatch(/当前项目卷宗报告/);
    expect(reportsTab.textContent).toMatch(/卷宗/);
    expect(reportsTab.textContent).not.toMatch(/预留/);
    expect(screen.getByTestId("hub-reports-pane")).toBeInTheDocument();
  });
});

describe("KnowledgeHubModal retrieval tab", () => {
  beforeEach(() => {
    useStore.setState({
      knowledgeHubTab: "retrieval",
      activeProjectId: "proj-demo",
    } as never);
  });

  it("shows the retrieval probe workbench", () => {
    render(<KnowledgeHubModal open onClose={() => undefined} />);
    expect(screen.getByTestId("hub-tab-retrieval")).toBeInTheDocument();
    expect(screen.getByTestId("hub-retrieval-pane")).toBeInTheDocument();
    expect(screen.getByTestId("hub-tab-retrieval").textContent).toMatch(/检索探针/);
  });
});

describe("KnowledgeHubModal memory tab (Wave 0: moved from Settings)", () => {
  beforeEach(() => {
    useStore.setState({
      knowledgeHubTab: "memory",
      activeProjectId: "proj-demo",
    } as never);
  });

  it("shows the memory management pane", () => {
    render(<KnowledgeHubModal open onClose={() => undefined} />);
    const tab = screen.getByTestId("hub-tab-memory");
    expect(tab.textContent).toMatch(/记忆/);
    expect(screen.getByTestId("memory-panel")).toBeInTheDocument();
  });
});
