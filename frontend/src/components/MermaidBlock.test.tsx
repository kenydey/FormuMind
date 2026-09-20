import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MermaidBlock from "./MermaidBlock";

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (_id: string, chart: string) => ({
      svg: `<svg data-testid="mermaid-svg">${chart}</svg>`,
    })),
  },
}));

describe("MermaidBlock", () => {
  it("renders svg from mermaid.render", async () => {
    render(<MermaidBlock chart={"flowchart LR\n  A --> B"} />);
    expect(await screen.findByTestId("wiki-mermaid-block")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("mermaid-svg").textContent).toMatch(/A --> B/);
    });
  });

  it("falls back to code block on render failure", async () => {
    const mermaid = await import("mermaid");
    vi.mocked(mermaid.default.render).mockRejectedValueOnce(new Error("bad chart"));
    render(<MermaidBlock chart={"not a diagram"} />);
    expect(await screen.findByTestId("wiki-mermaid-fallback")).toBeInTheDocument();
    expect(screen.getByText(/not a diagram/)).toBeInTheDocument();
  });
});
