/**
 * Leaderboard 卡片操作: removeFormula(删除) 与 saveFormulaToDoe(保存为 DOE 基准)。
 *
 * Pinned semantics:
 *  - removeFormula splices by index and schedules autosave; out-of-range is a no-op.
 *  - saveFormulaToDoe persists to formulation_versions (reusing the existing
 *    lineage when a same-name chain exists, else opening a new one), then sets
 *    requirement.active_formulation to a *copy* of the card (not a reference —
 *    later card edits must not silently mutate the saved DOE baseline).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  findFormulationLineages: vi.fn(),
  saveFormulationVersion: vi.fn(),
  scheduleAutosave: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      findFormulationLineages: mocks.findFormulationLineages,
      saveFormulationVersion: mocks.saveFormulationVersion,
    },
  };
});

import type { Formulation } from "../api";
import { useStore } from "./index";

function makeForm(name: string, tag?: string): Formulation {
  return {
    name,
    domain: "surface_treatment",
    ingredients: [
      { name: "环氧乳液", role: "resin", weight_pct: 30 },
      { name: tag ?? "", role: "additive", weight_pct: 2 },
    ],
    rationale: "测试",
    predicted: {},
    predicted_std: {},
    score: 0.9,
    warnings: [],
  } as unknown as Formulation;
}

function seed() {
  useStore.setState({
    leaderboard: [makeForm("配方A"), makeForm("配方B"), makeForm("配方C")],
    requirement: {
      domain: "surface_treatment",
      objectives: [],
      active_formulation: null,
    } as never,
    scheduleAutosave: mocks.scheduleAutosave,
  } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  seed();
});

describe("removeFormula", () => {
  it("按索引删除并触发 autosave", () => {
    useStore.getState().removeFormula(1);
    const names = useStore.getState().leaderboard.map((f) => f.name);
    expect(names).toEqual(["配方A", "配方C"]);
    expect(mocks.scheduleAutosave).toHaveBeenCalled();
  });

  it("越界索引是 no-op", () => {
    useStore.getState().removeFormula(9);
    expect(useStore.getState().leaderboard).toHaveLength(3);
  });
});

describe("saveFormulaToDoe", () => {
  it("同名链已存在 → 追加版本并设 active_formulation 为拷贝", async () => {
    mocks.findFormulationLineages.mockResolvedValue([
      { lineage_id: "lin-1", versions: [{ id: "v1" }, { id: "v2" }] },
    ]);
    mocks.saveFormulationVersion.mockResolvedValue({ id: "v3" });

    const result = await useStore.getState().saveFormulaToDoe(1);
    expect(result).toEqual({ version_id: "v3" });
    expect(mocks.saveFormulationVersion).toHaveBeenCalledWith(
      expect.objectContaining({ lineage_id: "lin-1", parent_version_id: "v2" })
    );

    const base = useStore.getState().requirement.active_formulation as Formulation;
    expect(base.name).toBe("配方B");
    // 快照: 修改卡片(原配方)不影响已保存基准
    useStore.getState().updateFormulaIngredient(1, 0, { weight_pct: 99 });
    expect(base.ingredients[0].weight_pct).toBe(30);
  });

  it("无同名链 → 开新链(不传 lineage_id)", async () => {
    mocks.findFormulationLineages.mockResolvedValue([]);
    mocks.saveFormulationVersion.mockResolvedValue({ id: "nv1" });

    await useStore.getState().saveFormulaToDoe(0);
    expect(mocks.saveFormulationVersion).toHaveBeenCalledWith(
      expect.objectContaining({
        formulation: expect.objectContaining({ name: "配方A" }),
        lineage_id: undefined,
        parent_version_id: undefined,
      })
    );
  });

  it("索引越界 → 返回 null 且不调用 API", async () => {
    const result = await useStore.getState().saveFormulaToDoe(7);
    expect(result).toBeNull();
    expect(mocks.saveFormulationVersion).not.toHaveBeenCalled();
  });

  it("stamps client_uid on DOE baseline and leaderboard card", async () => {
    mocks.findFormulationLineages.mockResolvedValue([]);
    mocks.saveFormulationVersion.mockResolvedValue({ id: "nv1" });

    await useStore.getState().saveFormulaToDoe(1);
    const base = useStore.getState().requirement.active_formulation as Formulation;
    const card = useStore.getState().leaderboard[1];
    expect(base.client_uid).toBeTruthy();
    expect(card.client_uid).toBe(base.client_uid);
  });

  it("lineage lookup failure surfaces (does not open a silent new chain)", async () => {
    mocks.findFormulationLineages.mockRejectedValue(new Error("network down"));
    await expect(useStore.getState().saveFormulaToDoe(0)).rejects.toThrow(/network down/);
    expect(mocks.saveFormulationVersion).not.toHaveBeenCalled();
  });
});
