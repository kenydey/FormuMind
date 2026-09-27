import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import McpApprovalCenter from "./McpApprovalCenter";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getMcpApprovalPending: vi.fn(),
      decideMcpApproval: vi.fn(),
    },
  };
});

const ITEM = {
  request_id: 7,
  server_id: "chem-tools",
  tool_name: "search_compound",
  session_id: "sess-1",
  project_id: null,
  age_s: 3.2,
};

function mockPending(items: typeof ITEM[]) {
  vi.mocked(api.getMcpApprovalPending).mockResolvedValue({ items, total: items.length });
}

describe("McpApprovalCenter (W3-11 wiring)", () => {
  beforeEach(() => {
    vi.mocked(api.getMcpApprovalPending).mockReset();
    vi.mocked(api.decideMcpApproval).mockReset();
    vi.mocked(api.decideMcpApproval).mockResolvedValue({
      ok: true,
      request_id: 7,
      decision: "allow",
      scope: "once",
    });
  });

  it("pops the dialog when a pending approval exists", async () => {
    mockPending([ITEM]);
    render(<McpApprovalCenter />);
    await waitFor(() =>
      expect(screen.getByTestId("mcp-approval-dialog")).toBeInTheDocument()
    );
    expect(screen.getByTestId("mcp-approval-server")).toHaveTextContent("chem-tools");
    expect(screen.getByTestId("mcp-approval-tool")).toHaveTextContent("search_compound");
  });

  it("renders nothing when there is no pending approval", async () => {
    mockPending([]);
    const { container } = render(<McpApprovalCenter />);
    await waitFor(() => expect(api.getMcpApprovalPending).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("submits the decision to the REST API and closes", async () => {
    const user = userEvent.setup();
    mockPending([ITEM]);
    render(<McpApprovalCenter />);
    await waitFor(() =>
      expect(screen.getByTestId("mcp-approval-dialog")).toBeInTheDocument()
    );
    // after deciding, backend no longer lists it
    mockPending([]);
    await user.click(screen.getByTestId("mcp-approval-allow-once"));
    await waitFor(() =>
      expect(api.decideMcpApproval).toHaveBeenCalledWith(7, "allow", "once")
    );
    await waitFor(() =>
      expect(screen.queryByTestId("mcp-approval-dialog")).not.toBeInTheDocument()
    );
  });

  it("deny passes scope once by default", async () => {
    const user = userEvent.setup();
    mockPending([ITEM]);
    render(<McpApprovalCenter />);
    await waitFor(() =>
      expect(screen.getByTestId("mcp-approval-dialog")).toBeInTheDocument()
    );
    mockPending([]);
    await user.click(screen.getByTestId("mcp-approval-deny"));
    await waitFor(() =>
      expect(api.decideMcpApproval).toHaveBeenCalledWith(7, "deny", "once")
    );
  });
});
