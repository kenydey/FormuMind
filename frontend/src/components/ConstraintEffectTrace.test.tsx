import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ConstraintEffectTrace from "./ConstraintEffectTrace";

describe("ConstraintEffectTrace", () => {
  it("renders wired / display_only / unwired chips", () => {
    render(
      <ConstraintEffectTrace
        items={[
          {
            field: "objectives.salt_spray_hours",
            kind: "objective",
            label: "salt_spray_hours",
            status: "wired",
            consumers: ["recommend"],
          },
          {
            field: "notes",
            kind: "meta",
            label: "notes",
            status: "display_only",
          },
          {
            field: "levers",
            kind: "lever",
            label: "levers",
            status: "unwired",
          },
        ]}
      />,
    );
    expect(screen.getByTestId("constraint-effect-trace")).toBeTruthy();
    expect(screen.getByText(/salt_spray_hours/)).toBeTruthy();
    expect(screen.getByText(/仅展示/)).toBeTruthy();
    expect(screen.getByText(/未接线/)).toBeTruthy();
  });

  it("returns null when empty", () => {
    const { container } = render(<ConstraintEffectTrace items={[]} />);
    expect(container.querySelector("[data-testid=constraint-effect-trace]")).toBeNull();
  });
});
