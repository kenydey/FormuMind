import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import McpApprovalDialog, { type McpApprovalRequest } from "./McpApprovalDialog";

const REQ: McpApprovalRequest = {
  id: 42,
  server_id: "chem-tools",
  tool_name: "search_compound",
  session_id: "sess-1",
  arguments: { name: "环氧树脂", limit: 5 },
  requested_at: 1727.5,
};

function renderDialog(overrides: Partial<Parameters<typeof McpApprovalDialog>[0]> = {}) {
  const onDecide = vi.fn().mockResolvedValue(undefined);
  const onClose = vi.fn();
  render(
    <McpApprovalDialog open request={REQ} onDecide={onDecide} onClose={onClose} {...overrides} />
  );
  return { onDecide, onClose };
}

describe("McpApprovalDialog", () => {
  it("shows server, tool, request id and arguments", () => {
    renderDialog();
    expect(screen.getByTestId("mcp-approval-server")).toHaveTextContent("chem-tools");
    expect(screen.getByTestId("mcp-approval-tool")).toHaveTextContent("search_compound");
    expect(screen.getByTestId("mcp-approval-dialog")).toHaveTextContent("42");
    expect(screen.getByTestId("mcp-approval-args")).toHaveTextContent("环氧树脂");
  });

  it("shows a placeholder when arguments are not recorded", () => {
    renderDialog({ request: { ...REQ, arguments: null } });
    expect(screen.queryByTestId("mcp-approval-args")).not.toBeInTheDocument();
    expect(screen.getByText("（参数未记录）")).toBeInTheDocument();
  });

  it("renders nothing when closed or without a request", () => {
    const { container } = render(
      <McpApprovalDialog open={false} request={REQ} onDecide={vi.fn()} onClose={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
    const { container: c2 } = render(
      <McpApprovalDialog open request={null} onDecide={vi.fn()} onClose={vi.fn()} />
    );
    expect(c2).toBeEmptyDOMElement();
  });

  it("deny submits (deny, once) and closes", async () => {
    const { onDecide, onClose } = renderDialog();
    fireEvent.click(screen.getByTestId("mcp-approval-deny"));
    await vi.waitFor(() => expect(onDecide).toHaveBeenCalledWith(42, "deny", "once"));
    expect(onClose).toHaveBeenCalled();
  });

  it("allow-once submits (allow, once)", async () => {
    const { onDecide } = renderDialog();
    fireEvent.click(screen.getByTestId("mcp-approval-allow-once"));
    await vi.waitFor(() => expect(onDecide).toHaveBeenCalledWith(42, "allow", "once"));
  });

  it("allow-session submits (allow, session)", async () => {
    const { onDecide } = renderDialog();
    fireEvent.click(screen.getByTestId("mcp-approval-allow-session"));
    await vi.waitFor(() => expect(onDecide).toHaveBeenCalledWith(42, "allow", "session"));
  });

  it("shows an error and stays open when onDecide rejects", async () => {
    const onDecide = vi.fn().mockRejectedValue(new Error("网络失败"));
    const onClose = vi.fn();
    render(<McpApprovalDialog open request={REQ} onDecide={onDecide} onClose={onClose} />);
    fireEvent.click(screen.getByTestId("mcp-approval-deny"));
    await vi.waitFor(() => expect(screen.getByText(/提交失败/)).toBeInTheDocument());
    expect(onClose).not.toHaveBeenCalled();
  });
});
