import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ArtifactSplitLayout from "./ArtifactSplitLayout";

describe("ArtifactSplitLayout", () => {
  it("renders left and right panes", () => {
    render(
      <ArtifactSplitLayout
        left={<button type="button">运行</button>}
        right={<div>产物内容</div>}
      />,
    );
    expect(screen.getByTestId("artifact-split-layout")).toBeInTheDocument();
    expect(screen.getByTestId("artifact-split-payload")).toHaveTextContent("产物内容");
    expect(screen.getByText("运行")).toBeInTheDocument();
    expect(screen.getByText("控制 · Controls")).toBeInTheDocument();
  });
});
