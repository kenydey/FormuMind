import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import ArtifactVersionsPanel from "./ArtifactVersionsPanel";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      createArtifactLineage: vi.fn(),
      listArtifactVersions: vi.fn(),
      restoreArtifactVersion: vi.fn(),
      getArtifactVersionDiff: vi.fn(),
    },
  };
});

const V1 = {
  version_id: "v1111111111111111",
  lineage_id: "lin00000000000001",
  based_on_version_id: null,
  status: "finalized",
  sha256: "aa",
  content_bytes: 10,
  created_at: 1727400000,
  actor: "cheng",
  finalized_at: 1727400100,
};

const V2 = {
  version_id: "v2222222222222222",
  lineage_id: "lin00000000000001",
  based_on_version_id: "v1111111111111111",
  status: "staging",
  sha256: "bb",
  content_bytes: 12,
  created_at: 1727400200,
  actor: null,
  finalized_at: null,
};

const LIST = {
  lineage: {
    lineage_id: "lin00000000000001",
    project_id: "proj-1",
    name: "配方报告",
    kind: "report",
    created_at: 1727399999,
    version_ids: [V1.version_id, V2.version_id],
  },
  versions: [V1, V2],
  graph: [
    { version_id: V1.version_id, based_on_version_id: null },
    { version_id: V2.version_id, based_on_version_id: V1.version_id },
  ],
};

const DIFF = {
  version_id: V2.version_id,
  against_id: V1.version_id,
  truncated: false,
  ops: [
    { type: "equal", old_text: "配方：", new_text: "配方：" },
    { type: "delete", old_text: "环氧 50%", new_text: "" },
    { type: "insert", old_text: "", new_text: "环氧 60%" },
  ],
};

describe("ArtifactVersionsPanel (W4-4)", () => {
  beforeEach(() => {
    vi.mocked(api.createArtifactLineage).mockReset();
    vi.mocked(api.listArtifactVersions).mockReset();
    vi.mocked(api.restoreArtifactVersion).mockReset();
    vi.mocked(api.getArtifactVersionDiff).mockReset();
    vi.mocked(api.listArtifactVersions).mockResolvedValue(LIST as never);
  });

  it("prompts to select a project when projectId is null", () => {
    render(<ArtifactVersionsPanel projectId={null} />);
    expect(screen.getByTestId("artifact-versions-panel")).toHaveTextContent("请先选择活动项目");
    expect(api.listArtifactVersions).not.toHaveBeenCalled();
  });

  it("loads and renders the version list with status badges and derivation", async () => {
    const user = userEvent.setup();
    render(<ArtifactVersionsPanel projectId="proj-1" />);
    await user.type(screen.getByTestId("artifact-lineage-input"), "lin00000000000001");
    await user.click(screen.getByTestId("artifact-lineage-load"));

    await waitFor(() =>
      expect(api.listArtifactVersions).toHaveBeenCalledWith("lin00000000000001"),
    );
    expect(screen.getByTestId(`artifact-version-row-${V1.version_id}`)).toHaveTextContent("已定版");
    expect(screen.getByTestId(`artifact-version-row-${V2.version_id}`)).toHaveTextContent("草稿");
    // derivation display: v2 derives from v1
    expect(screen.getByTestId(`artifact-version-row-${V2.version_id}`)).toHaveTextContent(
      "派生自",
    );
    expect(screen.getByTestId(`artifact-version-row-${V2.version_id}`)).toHaveTextContent(
      V1.version_id.slice(0, 8),
    );
  });

  it("calls restore and reloads the list (copy-on-write rollback)", async () => {
    const user = userEvent.setup();
    const restored = {
      ...V1,
      version_id: "v3333333333333333",
      based_on_version_id: V1.version_id,
      status: "staging",
    };
    vi.mocked(api.restoreArtifactVersion).mockResolvedValue(restored as never);
    render(<ArtifactVersionsPanel projectId="proj-1" />);
    await user.type(screen.getByTestId("artifact-lineage-input"), "lin00000000000001");
    await user.click(screen.getByTestId("artifact-lineage-load"));
    await waitFor(() =>
      expect(screen.getByTestId(`artifact-restore-${V1.version_id}`)).toBeInTheDocument(),
    );

    await user.click(screen.getByTestId(`artifact-restore-${V1.version_id}`));
    await waitFor(() =>
      expect(api.restoreArtifactVersion).toHaveBeenCalledWith(V1.version_id),
    );
    // list reloaded after restore
    await waitFor(() => expect(api.listArtifactVersions).toHaveBeenCalledTimes(2));
  });

  it("creates a lineage and loads its versions", async () => {
    const user = userEvent.setup();
    vi.mocked(api.createArtifactLineage).mockResolvedValue(LIST.lineage as never);
    render(<ArtifactVersionsPanel projectId="proj-1" />);
    await user.type(screen.getByTestId("artifact-lineage-name"), "配方报告");
    await user.click(screen.getByTestId("artifact-lineage-create"));

    await waitFor(() =>
      expect(api.createArtifactLineage).toHaveBeenCalledWith("proj-1", "配方报告"),
    );
    await waitFor(() =>
      expect(api.listArtifactVersions).toHaveBeenCalledWith("lin00000000000001"),
    );
  });

  it("diffs two selected versions with inline op highlighting", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getArtifactVersionDiff).mockResolvedValue(DIFF as never);
    render(<ArtifactVersionsPanel projectId="proj-1" />);
    await user.type(screen.getByTestId("artifact-lineage-input"), "lin00000000000001");
    await user.click(screen.getByTestId("artifact-lineage-load"));
    await waitFor(() =>
      expect(screen.getByTestId(`artifact-diff-check-${V1.version_id}`)).toBeInTheDocument(),
    );

    await user.click(screen.getByTestId(`artifact-diff-check-${V1.version_id}`));
    await user.click(screen.getByTestId(`artifact-diff-check-${V2.version_id}`));
    await user.click(screen.getByTestId("artifact-diff-run"));

    await waitFor(() =>
      expect(api.getArtifactVersionDiff).toHaveBeenCalledWith(V1.version_id, V2.version_id),
    );
    await waitFor(() =>
      expect(screen.getByTestId("artifact-diff-view")).toBeInTheDocument(),
    );
    const ops = screen.getAllByTestId("artifact-diff-op");
    expect(ops).toHaveLength(3);
    expect(ops[0]).toHaveAttribute("data-op-type", "equal");
    expect(ops[1]).toHaveAttribute("data-op-type", "delete");
    expect(ops[1]).toHaveTextContent("环氧 50%");
    expect(ops[2]).toHaveAttribute("data-op-type", "insert");
    expect(ops[2]).toHaveTextContent("环氧 60%");
  });

  it("shows truncated notice when the diff was truncated", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getArtifactVersionDiff).mockResolvedValue({
      ...DIFF,
      truncated: true,
    } as never);
    render(<ArtifactVersionsPanel projectId="proj-1" />);
    await user.type(screen.getByTestId("artifact-lineage-input"), "lin00000000000001");
    await user.click(screen.getByTestId("artifact-lineage-load"));
    await waitFor(() =>
      expect(screen.getByTestId(`artifact-diff-check-${V1.version_id}`)).toBeInTheDocument(),
    );
    await user.click(screen.getByTestId(`artifact-diff-check-${V1.version_id}`));
    await user.click(screen.getByTestId(`artifact-diff-check-${V2.version_id}`));
    await user.click(screen.getByTestId("artifact-diff-run"));

    await waitFor(() =>
      expect(screen.getByTestId("artifact-diff-view")).toHaveTextContent("大文本仅行级对比"),
    );
  });

  it("shows an error message when loading fails", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listArtifactVersions).mockRejectedValue(new Error("boom"));
    render(<ArtifactVersionsPanel projectId="proj-1" />);
    await user.type(screen.getByTestId("artifact-lineage-input"), "nope");
    await user.click(screen.getByTestId("artifact-lineage-load"));

    await waitFor(() =>
      expect(screen.getByTestId("artifact-versions-error")).toHaveTextContent("boom"),
    );
  });
});

describe("ArtifactVersionsPanel 回归（F-6）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("未知 status 兜底 staging 样式并显示未知，不渲染异常", async () => {
    const user = userEvent.setup();
    const weird = { ...V2, version_id: "v9999999999999999", status: "archived" };
    vi.mocked(api.listArtifactVersions).mockResolvedValue({
      ...LIST,
      versions: [weird],
      graph: [{ version_id: weird.version_id, based_on_version_id: null }],
    } as never);
    render(<ArtifactVersionsPanel projectId="proj-1" />);
    await user.type(screen.getByTestId("artifact-lineage-input"), "lin00000000000001");
    await user.click(screen.getByTestId("artifact-lineage-load"));
    const row = await screen.findByTestId(`artifact-version-row-${weird.version_id}`);
    // 兜底 staging 的 amber 样式
    expect(row.innerHTML).toMatch(/text-amber-300/);
    expect(row).toHaveTextContent("未知");
  });
});
