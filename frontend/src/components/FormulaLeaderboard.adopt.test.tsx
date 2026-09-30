import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import FormulaLeaderboard from "./FormulaLeaderboard";
import { useStore } from "../store";
import { api } from "../api";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      adoptRecommendation: vi.fn(async () => ({
        recommend_id: "rec-1",
        adopted: true,
        adopt_signal: "button",
      })),
    },
  };
});

const adoptRecommendation = vi.mocked(api.adoptRecommendation);

const FORMULA: Record<string, unknown> = {
  name: "测试配方",
  ingredients: [
    { name: "环氧树脂", wt_pct: 60 },
    { name: "固化剂", wt_pct: 40 },
  ],
  score: 0.9,
  warnings: [],
  predicted: {},
  predicted_std: {},
};

function seedStore(recommendId: string | null) {
  useStore.setState({
    leaderboard: [FORMULA],
    lastRecommendId: recommendId,
    requirement: useStore.getState().requirement,
    research: null,
    formulationBusy: false,
    busy: "idle",
  } as Record<string, unknown>);
}

describe("FormulaLeaderboard adopt button (C-8)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders 采纳 button when the card came from a /recommend round", () => {
    seedStore("rec-1");
    render(<FormulaLeaderboard />);
    expect(screen.getByTestId("adopt-button")).toHaveTextContent("采纳");
  });

  it("hides the adopt button when there is no recommend_id", () => {
    seedStore(null);
    render(<FormulaLeaderboard />);
    expect(screen.queryByTestId("adopt-button")).toBeNull();
  });

  it("clicking 采纳 calls the adopt API and shows 已采纳", async () => {
    seedStore("rec-1");
    const user = userEvent.setup();
    render(<FormulaLeaderboard />);
    await user.click(screen.getByTestId("adopt-button"));
    await waitFor(() => {
      expect(adoptRecommendation).toHaveBeenCalledTimes(1);
    });
    const [rid, body] = adoptRecommendation.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(rid).toBe("rec-1");
    expect(body.adopt_signal).toBe("button");
    expect(body.formula_index).toBe(0);
    expect(
      (body.formula_snapshot as Record<string, unknown>).name
    ).toBe("测试配方");
    await waitFor(() => {
      expect(screen.getByTestId("adopt-button")).toHaveTextContent("已采纳");
    });
  });
});
