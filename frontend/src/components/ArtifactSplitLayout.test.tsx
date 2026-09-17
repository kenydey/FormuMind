import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ArtifactSplitLayout from "./ArtifactSplitLayout";

describe("ArtifactSplitLayout", () => {
  it("stacks control above payload (full width, not side-by-side)", () => {
    render(
      <ArtifactSplitLayout
        left={<button type="button">运行</button>}
        right={<div>产物内容</div>}
      />,
    );
    const root = screen.getByTestId("artifact-split-layout");
    expect(root).toBeInTheDocument();
    expect(root).toHaveAttribute("data-layout", "stack");
    expect(root.className).toMatch(/flex-col/);
    expect(root.className).not.toMatch(/md:grid-cols-2/);
    expect(screen.getByTestId("artifact-split-payload")).toHaveTextContent("产物内容");
    expect(screen.getByText("运行")).toBeInTheDocument();
    expect(screen.getByText("控制 · Controls")).toBeInTheDocument();
  });
});
