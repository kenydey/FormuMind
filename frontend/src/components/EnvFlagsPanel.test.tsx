import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import EnvFlagsPanel from "./EnvFlagsPanel";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getEnvFlags: vi.fn(),
      postEnvFlags: vi.fn(),
    },
  };
});

function flag(attr: string, value: boolean, category = "kb", category_label = "知识库") {
  return {
    attr,
    env_key: `FORMUMIND_${attr.toUpperCase()}`,
    label: attr,
    description: "desc",
    category,
    category_label,
    hint: "",
    value,
    default: false,
  };
}

describe("EnvFlagsPanel focus anchor", () => {
  beforeEach(() => {
    useStore.setState({
      settingsEnvFocusAttr: "wiki_dossier_report_enabled",
      envFlagsRevision: 0,
    } as never);
    vi.mocked(api.getEnvFlags).mockReset();
    vi.mocked(api.postEnvFlags).mockReset();
    vi.mocked(api.getEnvFlags).mockResolvedValue({
      flags: [
        flag("gpu_enabled", false, "retrieval", "检索"),
        flag("wiki_enabled", true),
        flag("wiki_project_dossier_enabled", false),
        flag("wiki_dossier_report_enabled", false),
      ],
    });
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("highlights and scrolls to focusEnvAttr row", async () => {
    render(<EnvFlagsPanel />);
    const row = await screen.findByTestId("env-flag-wiki_dossier_report_enabled");
    expect(row).toHaveAttribute("data-focused", "true");
    expect(screen.getByTestId("env-flag-focus-badge-wiki_dossier_report_enabled")).toBeInTheDocument();
    await waitFor(() => {
      expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    });
  });

  it("bumps envFlagsRevision after successful save", async () => {
    const user = userEvent.setup();
    const savedFlags = [
      flag("wiki_enabled", true),
      flag("wiki_project_dossier_enabled", true),
      flag("wiki_dossier_report_enabled", true),
    ];
    vi.mocked(api.postEnvFlags).mockResolvedValue({
      updated: ["wiki_dossier_report_enabled"],
      rejected: [],
      flags: savedFlags,
    });
    render(<EnvFlagsPanel />);
    const reportRow = await screen.findByTestId("env-flag-wiki_dossier_report_enabled");
    const trueBtn = reportRow.querySelectorAll("button")[0];
    expect(trueBtn?.textContent).toMatch(/True/);
    await user.click(trueBtn!);
    await user.click(screen.getByRole("button", { name: /保存并生效/ }));
    await waitFor(() => {
      expect(api.postEnvFlags).toHaveBeenCalled();
    });
    expect(useStore.getState().envFlagsRevision).toBe(1);
  });

  it("W3-12: renders auto_audit_enabled toggle from backend registry", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getEnvFlags).mockResolvedValue({
      flags: [
        flag("auto_audit_enabled", false, "agent", "智能体"),
        flag("mcp_approval_enabled", false, "agent", "智能体"),
      ],
    });
    vi.mocked(api.postEnvFlags).mockResolvedValue({
      updated: ["auto_audit_enabled"],
      rejected: [],
      flags: [flag("auto_audit_enabled", true, "agent", "智能体")],
    });
    render(<EnvFlagsPanel />);
    const row = await screen.findByTestId("env-flag-auto_audit_enabled");
    expect(row).toBeInTheDocument();
    // toggle True then save → postEnvFlags called with the update
    const trueBtn = row.querySelectorAll("button")[0];
    await user.click(trueBtn!);
    await user.click(screen.getByRole("button", { name: /保存并生效/ }));
    await waitFor(() => {
      expect(api.postEnvFlags).toHaveBeenCalledWith({ auto_audit_enabled: true });
    });
  });
});
