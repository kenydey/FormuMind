import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ChatComposerPlus from "./ChatComposerPlus";

const toggleSelectedMcpServer = vi.fn();

vi.mock("../api", () => ({
  api: {
    listSkills: vi.fn(async () => ({ skills: [] })),
    listConnectors: vi.fn(async () => ({
      builtin: [],
      mcp: [
        {
          id: "filesystem",
          command: "npx",
          args: ["-y", "@modelcontextprotocol/server-filesystem"],
          enabled: true,
          env: {},
          transport: "stdio",
        },
      ],
      mcp_client_enabled: true,
      connectors_builtin_enabled: true,
    })),
  },
}));

vi.mock("../store", () => ({
  useStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      chatComposerPlusEnabled: true,
      chatMode: "chat",
      selectedChatSkills: [],
      selectedConnectors: [],
      selectedMcpServers: [],
      setChatMode: vi.fn(),
      toggleSelectedChatSkill: vi.fn(),
      toggleSelectedConnector: vi.fn(),
      toggleSelectedMcpServer,
      applyFormulationSkill: vi.fn(),
      sources: [],
      selectedSources: [],
      appendChatDraftRef: vi.fn(),
    }),
}));

vi.mock("zustand/react/shallow", () => ({
  useShallow: (fn: unknown) => fn,
}));

describe("ChatComposerPlus MCP servers", () => {
  beforeEach(() => {
    toggleSelectedMcpServer.mockClear();
  });

  it("lists enabled MCP servers and toggles selection", async () => {
    render(<ChatComposerPlus />);
    fireEvent.click(screen.getByTestId("chat-plus-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("mcp-server-filesystem")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("mcp-server-filesystem"));
    expect(toggleSelectedMcpServer).toHaveBeenCalledWith("filesystem");
  });
});
