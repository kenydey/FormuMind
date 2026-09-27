import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ConnectorsSettingsPanel from "./ConnectorsSettingsPanel";

const listConnectors = vi.fn();
const importMcpJson = vi.fn();
const confirmMcpImport = vi.fn();

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listConnectors: (...a: unknown[]) => listConnectors(...a),
      importMcpJson: (...a: unknown[]) => importMcpJson(...a),
      confirmMcpImport: (...a: unknown[]) => confirmMcpImport(...a),
      toggleBuiltinConnector: vi.fn(),
      setMcpServerEnabled: vi.fn(),
      deleteMcpServer: vi.fn(),
      probeMcpServer: vi.fn(),
    },
  };
});

describe("ConnectorsSettingsPanel MCP import", () => {
  beforeEach(() => {
    listConnectors.mockReset();
    importMcpJson.mockReset();
    confirmMcpImport.mockReset();
    listConnectors.mockResolvedValue({
      builtin: [
        {
          id: "literature",
          display_name: "Literature",
          kind: "literature",
          description: "lit",
          use_when: "",
          readonly: true,
          enabled: true,
        },
      ],
      mcp: [],
      mcp_client_enabled: false,
      connectors_builtin_enabled: true,
    });
  });

  it("previews pasted mcpServers JSON then confirms import", async () => {
    const user = userEvent.setup();
    importMcpJson.mockResolvedValue({
      ok: true,
      dry_run: true,
      import_id: "imp1",
      imported: false,
      detail: "ok",
      preview: {
        servers: [
          {
            id: "filesystem",
            command: "npx",
            args: ["-y", "pkg"],
            env_keys: [],
            transport: "stdio",
            enabled: false,
            warnings: [],
            errors: [],
          },
        ],
        source: "local",
        source_url: "",
        warnings: [],
        errors: [],
      },
    });
    confirmMcpImport.mockResolvedValue({
      ok: true,
      dry_run: false,
      imported: true,
      detail: "done",
      mcp: [
        {
          id: "filesystem",
          command: "npx",
          args: ["-y", "pkg"],
          enabled: false,
          transport: "stdio",
        },
      ],
    });

    render(<ConnectorsSettingsPanel />);
    await waitFor(() => expect(screen.getByText("Literature")).toBeInTheDocument());
    await user.click(screen.getByTestId("mcp-add-paste"));
    fireEvent.change(screen.getByTestId("mcp-paste-json"), {
      target: {
        value: '{"mcpServers":{"filesystem":{"command":"npx","args":["-y","pkg"]}}}',
      },
    });
    await user.click(screen.getByText("预览"));
    await waitFor(() => expect(screen.getByTestId("mcp-import-preview")).toBeInTheDocument());
    expect(importMcpJson).toHaveBeenCalled();
    await user.click(screen.getByTestId("mcp-import-confirm"));
    await waitFor(() => expect(screen.getByTestId("mcp-row-filesystem")).toBeInTheDocument());
  });
});
