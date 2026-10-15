import { render, screen, fireEvent, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type SecretStatus } from "../api";
import ApiSettingsPanel from "./ApiSettingsPanel";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: { ...actual.api, getSecrets: vi.fn(), updateSecrets: vi.fn(), testSecret: vi.fn() } };
});

const SECRETS: SecretStatus[] = [
  { id: "openalex_api_key", env_key: "FORMUMIND_OPENALEX_API_KEY", label: "OpenAlex 内容库", group: "research", set: false, masked: "", secret: true },
  { id: "semantic_scholar_api_key", env_key: "FORMUMIND_SEMANTIC_SCHOLAR_API_KEY", label: "Semantic Scholar API Key", group: "research", set: true, masked: "••••", secret: true },
  { id: "unpaywall_mailto", env_key: "FORMUMIND_UNPAYWALL_MAILTO", label: "Unpaywall 邮箱", group: "research", set: true, masked: "me@lab.org", secret: false },
  { id: "langfuse_secret_key", env_key: "FORMUMIND_LANGFUSE_SECRET_KEY", label: "Langfuse Secret Key", group: "observability", set: false, masked: "", secret: true },
];

describe("ApiSettingsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSecrets).mockResolvedValue({ secrets: SECRETS });
  });

  it("lists Semantic Scholar under 学术 / 研究 and the new observability group", async () => {
    render(<ApiSettingsPanel />);
    const research = (await screen.findByText("学术 / 研究")).closest("section")!;
    expect(within(research).getByText("Semantic Scholar API Key")).toBeTruthy();
    expect(within(research).getByText("FORMUMIND_SEMANTIC_SCHOLAR_API_KEY")).toBeTruthy();
    expect(screen.getByText("可观测性 / 追踪")).toBeTruthy();
  });

  it("shows an e-mail in full and edits it as plain text, but hides a key", async () => {
    render(<ApiSettingsPanel />);
    const row = (label: string) => screen.getByText(label).closest("div.rounded-lg") as HTMLElement;
    await screen.findByText("Unpaywall 邮箱");
    expect(within(row("Unpaywall 邮箱")).getByText("me@lab.org")).toBeTruthy();

    fireEvent.click(within(row("Unpaywall 邮箱")).getByText("修改"));
    expect((within(row("Unpaywall 邮箱")).getByPlaceholderText(/当前 me@lab.org/) as HTMLInputElement).type).toBe("text");

    fireEvent.click(within(row("Semantic Scholar API Key")).getByText("修改"));
    expect((within(row("Semantic Scholar API Key")).getByPlaceholderText(/当前/) as HTMLInputElement).type).toBe("password");
  });

  it("offers 测试连接 for a configured research key", async () => {
    render(<ApiSettingsPanel />);
    await screen.findByText("Semantic Scholar API Key");
    const row = screen.getByText("Semantic Scholar API Key").closest("div.rounded-lg") as HTMLElement;
    expect(within(row).getByText(/测试/)).toBeTruthy();
  });
});
