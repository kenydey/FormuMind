import { render, screen, waitFor } from "@testing-library/react";
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
    } as never);
    vi.mocked(api.getEnvFlags).mockReset();
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
});
