/**
 * Revision history modal.
 *
 * The reason history exists is the diff, so these check that the view answers
 * "what changed" rather than listing frozen copies — including the
 * distinction between swapping a component and re-balancing the ratios.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Formulation, FormulationVersionView, VersionDiffResult } from "../api";
import VersionHistoryModal from "./VersionHistoryModal";

const form = {
  name: "Primer A",
  domain: "anticorrosion_coating",
  ingredients: [{ name: "Epoxy", role: "resin", weight_pct: 60 }],
  predicted: {},
  warnings: [],
} as unknown as Formulation;

const version = (over: Partial<FormulationVersionView> = {}): FormulationVersionView =>
  ({
    id: "v1",
    lineage_id: "lin-1",
    version: 1,
    parent_version_id: null,
    name: "Primer A",
    domain: "anticorrosion_coating",
    change_summary: "baseline",
    created_by: "wang",
    created_at: "2026-07-28T00:00:00",
    ...over,
  }) as FormulationVersionView;

const diff = (over: Partial<VersionDiffResult> = {}): VersionDiffResult =>
  ({
    from_version: 1,
    to_version: 2,
    change_summary: "调整 1 项",
    topology_changed: false,
    renamed: null,
    ingredient_changes: [
      {
        name: "Zinc phosphate",
        change: "adjusted",
        role: "inhibitor",
        before_pct: 4,
        after_pct: 9,
        delta_pct: 5,
      },
    ],
    metric_deltas: {
      cost_cny_per_kg: { before: 18, after: 21, delta: 3, pct: 16.7 },
    },
    ...over,
  }) as VersionDiffResult;

function mockLineage(versions: FormulationVersionView[]) {
  vi.spyOn(api, "findFormulationLineages").mockResolvedValue(
    versions.length ? [{ lineage_id: "lin-1", versions }] : []
  );
}

beforeEach(() => vi.restoreAllMocks());

describe("VersionHistoryModal", () => {
  it("looks the lineage up by formulation name", async () => {
    mockLineage([version()]);
    render(<VersionHistoryModal form={form} />);
    await waitFor(() =>
      expect(api.findFormulationLineages).toHaveBeenCalledWith(
        "Primer A",
        "anticorrosion_coating",
        1
      )
    );
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText("baseline")).toBeInTheDocument();
  });

  it("invites a first save when there is no history yet", async () => {
    mockLineage([]);
    render(<VersionHistoryModal form={form} />);
    await waitFor(() => expect(screen.getByText(/尚无修订记录/)).toBeInTheDocument());
  });

  it("offers a comparison only where there is a previous revision", async () => {
    mockLineage([version(), version({ id: "v2", version: 2, parent_version_id: "v1" })]);
    render(<VersionHistoryModal form={form} />);
    await waitFor(() => expect(screen.getByText("v2")).toBeInTheDocument());
    // v1 has nothing before it, so exactly one compare button.
    expect(screen.getAllByRole("button", { name: /对比/ })).toHaveLength(1);
  });

  it("shows what changed, not just that something did", async () => {
    mockLineage([version(), version({ id: "v2", version: 2, parent_version_id: "v1" })]);
    vi.spyOn(api, "diffFormulationVersions").mockResolvedValue(diff());

    render(<VersionHistoryModal form={form} />);
    await userEvent.click(await screen.findByRole("button", { name: /对比/ }));

    await waitFor(() => expect(screen.getByText("Zinc phosphate")).toBeInTheDocument());
    expect(screen.getByText("调整")).toBeInTheDocument();
    expect(screen.getByText("4 → 9")).toBeInTheDocument();
    expect(screen.getByText("+16.7%")).toBeInTheDocument();
  });

  it("separates a component swap from a re-balance", async () => {
    mockLineage([version(), version({ id: "v2", version: 2, parent_version_id: "v1" })]);
    vi.spyOn(api, "diffFormulationVersions").mockResolvedValue(
      diff({ topology_changed: true })
    );

    render(<VersionHistoryModal form={form} />);
    await userEvent.click(await screen.findByRole("button", { name: /对比/ }));
    await waitFor(() => expect(screen.getByText("成分组成变化")).toBeInTheDocument());
    expect(screen.queryByText("仅配比调整")).not.toBeInTheDocument();
  });

  it("chains a new revision onto the latest one", async () => {
    mockLineage([version(), version({ id: "v2", version: 2, parent_version_id: "v1" })]);
    const save = vi.spyOn(api, "saveFormulationVersion").mockResolvedValue(
      version({ id: "v3", version: 3 })
    );

    render(<VersionHistoryModal form={form} />);
    await screen.findByText("v2");
    await userEvent.type(screen.getByPlaceholderText(/本次修改说明/), "raise inhibitor");
    await userEvent.click(screen.getByRole("button", { name: /保存为新版本/ }));

    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][0]).toMatchObject({
      lineage_id: "lin-1",
      parent_version_id: "v2", // the tip, not the root
      change_summary: "raise inhibitor",
    });
  });

  it("starts a lineage when none exists", async () => {
    mockLineage([]);
    const save = vi.spyOn(api, "saveFormulationVersion").mockResolvedValue(version());

    render(<VersionHistoryModal form={form} />);
    await screen.findByText(/尚无修订记录/);
    await userEvent.click(screen.getByRole("button", { name: /保存为新版本/ }));

    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][0]).toMatchObject({
      lineage_id: null,
      parent_version_id: null,
    });
  });

  it("flags a revision whose parent was deleted", async () => {
    // SET NULL on delete keeps descendants alive; the chain is broken, and
    // showing it as a root would misrepresent the history.
    mockLineage([version({ id: "v2", version: 2, parent_version_id: null })]);
    render(<VersionHistoryModal form={form} />);
    await waitFor(() => expect(screen.getByText(/父版本已删除/)).toBeInTheDocument());
  });

  it("surfaces a failed save", async () => {
    mockLineage([version()]);
    vi.spyOn(api, "saveFormulationVersion").mockRejectedValue(new Error("配方版本保存失败"));

    render(<VersionHistoryModal form={form} />);
    await screen.findByText("v1");
    await userEvent.click(screen.getByRole("button", { name: /保存为新版本/ }));
    await waitFor(() => expect(screen.getByText("配方版本保存失败")).toBeInTheDocument());
  });
});
