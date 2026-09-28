import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
