import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import LiteratureFreezeStrip from "./LiteratureFreezeStrip";

const capture = vi.fn();
const freeze = vi.fn();
const screenApi = vi.fn();
const getMan = vi.fn();
const getPresets = vi.fn();
const getVersions = vi.fn();
const saveVersion = vi.fn();
const rollbackVersion = vi.fn();
const evaluateApi = vi.fn();

vi.mock("../../api", () => ({
  api: {
    getLiteratureManifest: (projectId: string) => getMan(projectId),
    captureLiteratureManifest: (body: unknown) => capture(body),
    freezeLiteratureManifest: (body: unknown) => freeze(body),
    unfreezeLiteratureManifest: vi.fn(async () => ({})),
    screenLiteratureManifest: (body: unknown) => screenApi(body),
    getScreeningPresets: () => getPresets(),
    getScreeningRuleVersions: (projectId: string) => getVersions(projectId),
    saveScreeningRuleVersion: (body: unknown) => saveVersion(body),
    rollbackScreeningRuleVersion: (body: unknown) => rollbackVersion(body),
    evaluateScreening: (body: unknown) => evaluateApi(body),
  },
  awaitTaskStream: vi.fn(async () => ({})),
  formatApiError: (e: unknown) => String(e),
}));

describe("LiteratureFreezeStrip", () => {
  beforeEach(() => {
    capture.mockReset();
    freeze.mockReset();
    screenApi.mockReset();
    getMan.mockReset();
    capture.mockResolvedValue({});
    freeze.mockResolvedValue({});
    screenApi.mockResolvedValue({});
    getMan.mockResolvedValue({
      project_id: "p1",
      items: [{ id: "a", title: "Epoxy", screening: "unset" }],
      frozen: null,
      coverage: { candidate_count: 1, frozen_count: 0 },
    });
    getPresets.mockReset();
    getVersions.mockReset();
    saveVersion.mockReset();
    rollbackVersion.mockReset();
    evaluateApi.mockReset();
    getPresets.mockResolvedValue({
      presets: [{ name: "anticorrosion_coating", title: "防腐涂料" }],
    });
    getVersions.mockResolvedValue({
      versions: [],
      history: [],
      current_rule_name: null,
      current_rule_version: null,
    });
    saveVersion.mockResolvedValue({});
    rollbackVersion.mockResolvedValue({ summary: {} });
    evaluateApi.mockResolvedValue({ evaluated: false, labeled_count: 2 });
  });

  it("loads stats and capture/freeze", async () => {
    render(<LiteratureFreezeStrip projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByTestId("literature-freeze-stats").textContent).toMatch(/候选 1/);
    });
    fireEvent.click(screen.getByTestId("literature-capture-btn"));
    await waitFor(() => expect(capture).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("literature-freeze-btn"));
    await waitFor(() => expect(freeze).toHaveBeenCalled());
  });

  it("runs screening form", async () => {
    render(<LiteratureFreezeStrip projectId="p1" />);
    await waitFor(() => screen.getByTestId("literature-screen-toggle"));
    fireEvent.click(screen.getByTestId("literature-screen-toggle"));
    fireEvent.click(screen.getByTestId("literature-screen-btn"));
    await waitFor(() => expect(screenApi).toHaveBeenCalled());
  });

  it("W6-2: preset selector renders and passes preset", async () => {
    render(<LiteratureFreezeStrip projectId="p1" />);
    await waitFor(() => screen.getByTestId("literature-screen-toggle"));
    fireEvent.click(screen.getByTestId("literature-screen-toggle"));
    await waitFor(() =>
      expect(screen.getByTestId("literature-screen-preset")).toBeTruthy(),
    );
    fireEvent.change(screen.getByTestId("literature-screen-preset"), {
      target: { value: "anticorrosion_coating" },
    });
    fireEvent.click(screen.getByTestId("literature-screen-btn"));
    await waitFor(() => expect(screenApi).toHaveBeenCalled());
    expect(screenApi.mock.calls[0][0]).toMatchObject({
      preset: "anticorrosion_coating",
    });
  });

  it("W6-2: saves named rule version and evaluates", async () => {
    render(<LiteratureFreezeStrip projectId="p1" />);
    await waitFor(() => screen.getByTestId("literature-screen-toggle"));
    fireEvent.click(screen.getByTestId("literature-screen-toggle"));
    fireEvent.change(screen.getByTestId("literature-rule-name"), {
      target: { value: "v1" },
    });
    fireEvent.click(screen.getByTestId("literature-rule-save-btn"));
    await waitFor(() => expect(saveVersion).toHaveBeenCalled());
    expect(saveVersion.mock.calls[0][0]).toMatchObject({
      project_id: "p1",
      name: "v1",
    });
    fireEvent.click(screen.getByTestId("literature-eval-btn"));
    await waitFor(() => expect(evaluateApi).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId("literature-eval-result")).toBeTruthy(),
    );
  });

  it("W6-2: rule version history timeline renders", async () => {
    getVersions.mockResolvedValue({
      versions: [
        {
          name: "v1",
          version: "abc123",
          criteria: {},
          created_by: "hub",
          created_at: 1700000000,
          changelog: "首版",
        },
      ],
      history: [
        {
          at: 1700000000,
          actor: "hub",
          action: "saved",
          name: "v1",
          version: "abc123",
          changelog: "首版",
        },
      ],
      current_rule_name: "v1",
      current_rule_version: "abc123",
    });
    render(<LiteratureFreezeStrip projectId="p1" />);
    await waitFor(() => screen.getByTestId("literature-screen-toggle"));
    fireEvent.click(screen.getByTestId("literature-screen-toggle"));
    await waitFor(() =>
      expect(screen.getByTestId("literature-rule-versions")).toBeTruthy(),
    );
    expect(
      screen.getByTestId("literature-rule-history").textContent,
    ).toContain("首版");
  });
});

describe("LiteratureFreezeStrip 回归（F-5）", () => {
  beforeEach(() => {
    getVersions.mockReset();
    getVersions.mockResolvedValue({
      versions: [{ version: "v12345678", name: "r1", changelog: "init" }],
      history: [],
      current_rule_name: "r1",
      current_rule_version: "v12345678",
    });
    getMan.mockReset();
    getMan.mockResolvedValue({
      project_id: "p1",
      items: [],
      frozen: null,
      coverage: { candidate_count: 0, frozen_count: 0 },
    });
    rollbackVersion.mockReset();
    rollbackVersion.mockResolvedValue({ summary: {} });
  });

  it("回滚 select 为受控组件：未选择时按钮禁用，选中后回滚用该值", async () => {
    render(<LiteratureFreezeStrip projectId="p1" />);
    fireEvent.click(screen.getByTestId("literature-screen-toggle"));
    const select = await screen.findByTestId("literature-rule-version-select");
    const btn = screen.getByTestId("literature-rule-rollback-btn");
    expect(btn).toBeDisabled();
    fireEvent.change(select, { target: { value: "v12345678" } });
    expect(select).toHaveValue("v12345678");
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    await waitFor(() =>
      expect(rollbackVersion).toHaveBeenCalledWith(
        expect.objectContaining({ version: "v12345678" }),
      ),
    );
  });
});

describe("LiteratureFreezeStrip 回归（F-4）", () => {
  beforeEach(() => {
    capture.mockReset();
    getMan.mockReset();
    getPresets.mockReset();
    getVersions.mockReset();
    capture.mockResolvedValue({});
    getPresets.mockResolvedValue({ presets: [] });
    getVersions.mockResolvedValue({
      versions: [],
      history: [],
      current_rule_name: null,
      current_rule_version: null,
    });
  });

  it("动作耗时中切换 project，旧 project 的刷新不覆盖新 project 数据", async () => {
    const manA = {
      project_id: "pA",
      items: [],
      frozen: null,
      coverage: { candidate_count: 5, frozen_count: 0 },
    };
    const manB = {
      project_id: "pB",
      items: [],
      frozen: null,
      coverage: { candidate_count: 9, frozen_count: 0 },
    };
    getMan.mockImplementation((pid: string) =>
      Promise.resolve(pid === "pA" ? manA : manB),
    );
    let resolveCapture!: () => void;
    capture.mockImplementation(
      () =>
        new Promise<void>((res) => {
          resolveCapture = res;
        }),
    );
    const { rerender } = render(<LiteratureFreezeStrip projectId="pA" />);
    await waitFor(() =>
      expect(
        screen.getByTestId("literature-freeze-stats").textContent,
      ).toMatch(/候选 5/),
    );
    // pA 上点 Capture，动作挂起
    fireEvent.click(screen.getByTestId("literature-capture-btn"));
    await waitFor(() => expect(capture).toHaveBeenCalled());
    // 切换到 pB
    rerender(<LiteratureFreezeStrip projectId="pB" />);
    await waitFor(() =>
      expect(
        screen.getByTestId("literature-freeze-stats").textContent,
      ).toMatch(/候选 9/),
    );
    const pACallsBefore = getMan.mock.calls.filter((c) => c[0] === "pA").length;
    // pA 的 capture 完成：旧闭包 reload(pA) 不得再发起、不得覆盖 pB 面板
    await act(async () => {
      resolveCapture();
    });
    expect(
      screen.getByTestId("literature-freeze-stats").textContent,
    ).toMatch(/候选 9/);
    expect(getMan.mock.calls.filter((c) => c[0] === "pA").length).toBe(
      pACallsBefore,
    );
    // 动作完成后的刷新针对的是当前 project（pB），而非旧 project
    expect(getMan).toHaveBeenLastCalledWith("pB");
  });
});
