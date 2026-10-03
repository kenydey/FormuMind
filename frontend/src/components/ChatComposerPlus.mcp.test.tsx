import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ChatComposerPlus from "./ChatComposerPlus";

const toggleSelectedMcpServer = vi.fn();
const getEnvFlags = vi.fn();

vi.mock("../api", () => ({
  api: {
    getEnvFlags: () => getEnvFlags(),
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

const storeState = { envFlagsRevision: 0 };

vi.mock("../store", () => ({
  useStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      envFlagsRevision: storeState.envFlagsRevision,
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
    getEnvFlags.mockReset();
    getEnvFlags.mockResolvedValue({ flags: [] });
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

describe("ChatComposerPlus ← Settings toggle chat_composer_plus_enabled", () => {
  beforeEach(() => {
    getEnvFlags.mockReset();
    storeState.envFlagsRevision = 0;
  });

  it("shows the + menu by default (flag absent or API not answered yet)", async () => {
    getEnvFlags.mockResolvedValue({ flags: [] });
    render(<ChatComposerPlus />);
    expect(screen.getByTestId("chat-composer-plus")).toBeTruthy();
    await waitFor(() => expect(getEnvFlags).toHaveBeenCalled());
    expect(screen.getByTestId("chat-composer-plus")).toBeTruthy();
  });

  it("hides the + menu when the backend flag is off", async () => {
    getEnvFlags.mockResolvedValue({
      flags: [{ attr: "chat_composer_plus_enabled", value: false }],
    });
    render(<ChatComposerPlus />);
    await waitFor(() => expect(screen.queryByTestId("chat-composer-plus")).toBeNull());
  });

  it("keeps the + menu when the flag request fails", async () => {
    getEnvFlags.mockRejectedValue(new Error("offline"));
    render(<ChatComposerPlus />);
    await waitFor(() => expect(getEnvFlags).toHaveBeenCalled());
    expect(screen.getByTestId("chat-composer-plus")).toBeTruthy();
  });

  it("re-reads the flag after the Settings dialog saved (revision bump)", async () => {
    getEnvFlags.mockResolvedValue({
      flags: [{ attr: "chat_composer_plus_enabled", value: true }],
    });
    const { rerender } = render(<ChatComposerPlus />);
    await waitFor(() => expect(getEnvFlags).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("chat-composer-plus")).toBeTruthy();

    getEnvFlags.mockResolvedValue({
      flags: [{ attr: "chat_composer_plus_enabled", value: false }],
    });
    storeState.envFlagsRevision = 1;
    rerender(<ChatComposerPlus />);
    await waitFor(() => expect(screen.queryByTestId("chat-composer-plus")).toBeNull());
    expect(getEnvFlags).toHaveBeenCalledTimes(2);
  });
});
