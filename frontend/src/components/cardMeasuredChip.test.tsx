import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { cardMeasuredChip } from "./kgMeasuredObservability";

// Lightweight stand-in that mirrors FormulaLeaderboard header chip wiring
function ChipHost({
  hits,
  materials,
}: {
  hits?: { material: string; metric: string; quality: string }[];
  materials?: string[];
}) {
  const chip = cardMeasuredChip(hits as any, materials);
  if (!chip) return null;
  return (
    <span
      data-testid="card-measured-chip"
      data-quality={chip.quality}
      className={chip.className}
      title={chip.title}
    >
      {chip.label}
    </span>
  );
}

describe("card-measured-chip host", () => {
  it("renders collapsed-visible chip for good hits", () => {
    render(
      <ChipHost
        hits={[
          {
            material: "GPTMS",
            metric: "salt_spray_hours",
            quality: "good",
          },
        ]}
      />,
    );
    const el = screen.getByTestId("card-measured-chip");
    expect(el.textContent).toBe("实测加成");
    expect(el.getAttribute("data-quality")).toBe("good");
  });
});

vi.mock("../api", () => ({ api: {} }));
