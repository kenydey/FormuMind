import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SkillsSettingsPanel from "./SkillsSettingsPanel";

const listSkills = vi.fn();
const installSkillPaste = vi.fn();
const confirmSkillInstall = vi.fn();

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listSkills: (...args: unknown[]) => listSkills(...args),
      installSkillPaste: (...args: unknown[]) => installSkillPaste(...args),
      confirmSkillInstall: (...args: unknown[]) => confirmSkillInstall(...args),
      patchSkillsPrefs: vi.fn(),
      uninstallSkill: vi.fn(),
    },
  };
});

describe("SkillsSettingsPanel install", () => {
  beforeEach(() => {
    listSkills.mockReset();
    installSkillPaste.mockReset();
    confirmSkillInstall.mockReset();
    listSkills.mockResolvedValue({
      skills: [
        {
          id: "literature-review",
          kind: "chat_skill",
          title: "Literature",
          summary: "bundled",
          action: "chat",
          modal: null,
          icon: "📘",
          tools: [],
          checklist: [],
          presets: {},
          activation_policy: "user-controlled",
          origin: "bundled",
          enabled: true,
          pinned: false,
        },
      ],
      prefs: {},
    });
  });

  it("previews paste then confirms one-click install", async () => {
    const user = userEvent.setup();
    installSkillPaste.mockResolvedValue({
      ok: true,
      dry_run: true,
      install_id: "abc",
      skill_id: "formula-method-notes",
      installed: false,
      detail: "预览通过",
      preview: {
        name: "formula-method-notes",
        description: "demo",
        summary: "demo",
        allowed_tools: ["kb_hybrid"],
        rejected_tools: [],
        origin: "local",
        source_url: "",
        pinned_sha: "",
        file_count: 1,
        warnings: [],
        errors: [],
      },
    });
    confirmSkillInstall.mockResolvedValue({
      ok: true,
      dry_run: false,
      installed: true,
      skill_id: "formula-method-notes",
      detail: "已安装",
      catalog: {
        skills: [
          {
            id: "formula-method-notes",
            kind: "chat_skill",
            title: "demo",
            summary: "demo",
            action: "chat",
            modal: null,
            icon: "📘",
            tools: ["kb_hybrid"],
            checklist: [],
            presets: {},
            activation_policy: "user-controlled",
            origin: "local",
            enabled: true,
            pinned: false,
          },
        ],
        prefs: {},
      },
    });

    render(<SkillsSettingsPanel />);
    await waitFor(() => expect(screen.getByTestId("skill-row-literature-review")).toBeInTheDocument());
    await user.click(screen.getByTestId("skills-add-paste"));
    await user.type(
      screen.getByTestId("skills-paste-md"),
      "---\nname: formula-method-notes\ndescription: demo\nallowed_tools: kb_hybrid\n---\n\n# hi",
    );
    await user.click(screen.getByText("预览"));
    await waitFor(() => expect(screen.getByTestId("skills-install-preview")).toBeInTheDocument());
    expect(screen.getByText("formula-method-notes")).toBeInTheDocument();
    await user.click(screen.getByTestId("skills-install-confirm"));
    await waitFor(() =>
      expect(screen.getByTestId("skill-row-formula-method-notes")).toBeInTheDocument(),
    );
    expect(screen.getByText("local")).toBeInTheDocument();
  });
});
