import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import WikiMarkdownReader from "./WikiMarkdownReader";

vi.mock("./MermaidBlock", () => ({
  default: ({ chart }: { chart: string }) => (
    <div data-testid="wiki-mermaid-block">{chart}</div>
  ),
}));

describe("WikiMarkdownReader mermaid (S3)", () => {
  it("renders mermaid fence via MermaidBlock", async () => {
    const md = [
      "---",
      "kind: theme",
      "title: Mermaid demo",
      "---",
      "",
      "# Flow",
      "",
      "```mermaid",
      "flowchart LR",
      "  A[Raw] --> B[Wiki]",
      "```",
      "",
    ].join("\n");

    render(
      <WikiMarkdownReader
        page={{
          path: "themes/demo.md",
          title: "Mermaid demo",
          kind: "theme",
          markdown: md,
        }}
      />,
    );

    expect(await screen.findByTestId("wiki-markdown-reader")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("wiki-mermaid-block").textContent).toMatch(/flowchart LR/);
    });
  });

  it("leaves plain code fences alone", async () => {
    const md = "# x\n\n```js\nconsole.log(1)\n```\n";
    render(
      <WikiMarkdownReader
        page={{ path: "themes/x.md", title: "x", markdown: md }}
      />,
    );
    expect(await screen.findByTestId("wiki-markdown-reader")).toBeInTheDocument();
    expect(screen.queryByTestId("wiki-mermaid-block")).not.toBeInTheDocument();
    expect(screen.getByText(/console\.log/)).toBeInTheDocument();
  });
});
