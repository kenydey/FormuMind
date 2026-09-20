import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ThinkingTimeline from "./ThinkingTimeline";

describe("ThinkingTimeline", () => {
  it("renders nothing when steps are empty", () => {
    const { container } = render(<ThinkingTimeline steps={[]} />);
    expect(container.querySelector("[data-testid=thinking-timeline]")).toBeNull();
  });

  it("renders running and done steps", () => {
    render(
      <ThinkingTimeline
        steps={[
          { id: "retrieve", title: "正在检索…", status: "done" },
          { id: "grade", title: "正在评估…", status: "running", detail: "CRAG" },
        ]}
      />,
    );
    expect(screen.getByTestId("thinking-timeline")).toBeInTheDocument();
    expect(screen.getByTestId("thinking-step-retrieve")).toHaveAttribute("data-status", "done");
    expect(screen.getByTestId("thinking-step-grade")).toHaveAttribute("data-status", "running");
    expect(screen.getByText("正在评估…")).toBeInTheDocument();
    expect(screen.getByText("CRAG")).toBeInTheDocument();
  });
});
