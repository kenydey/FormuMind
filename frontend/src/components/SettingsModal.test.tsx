/**
 * The LLM settings tab: two peer sections, not a global model plus an add-on.
 *
 * This exists because of a specific piece of user feedback. The text section had
 * no heading — it was just "供应商 / 模型 / API Key" — so it read as *the* global
 * model, and the shared 关闭/保存 row sat between the two sections, which made the
 * vision block look like an afterthought. Someone with the feature already
 * shipped and running asked for the feature to be built.
 *
 * DOM order is the thing being pinned. It is exactly what tests can check about a
 * layout, and it is what went wrong.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type LLMSettingsResponse } from "../api";
import { useStore } from "../store";
import type { AppState } from "../store/types";
import SettingsModal from "./SettingsModal";

const SETTINGS: LLMSettingsResponse = {
  provider: "anthropic",
  model: "claude-sonnet-4-6",
  key_set: true,
  base_url: undefined,
  providers: [
    { id: "anthropic", label: "Anthropic (Claude)", models: [{ id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", recommended: true }] },
    { id: "openai", label: "OpenAI", models: [{ id: "gpt-4o", label: "GPT-4o", recommended: true }] },
    { id: "custom", label: "OpenAI 兼容自定义端点", base_url: null, models: [] },
  ],
  vision: {
    provider: "",
    model: "claude-sonnet-4-6",
    base_url: null,
    key_set: true,
    inherits: true,
    configured: true,
    hint: "",
  },
};

/** Vertical position of the first element whose text contains `needle`. */
function topOf(needle: string): number {
  const el = screen.getByText(new RegExp(needle));
  return Array.from(document.body.querySelectorAll("*")).indexOf(el);
}

describe("SettingsModal — LLM tab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getSettings").mockResolvedValue(SETTINGS);
    vi.spyOn(api, "getAuthStatus").mockResolvedValue({ auth_required: false, hint: "" });
    useStore.setState({
      settingsOpen: true,
      settingsTab: "llm",
      llmConfig: { provider: "anthropic", model: "claude-sonnet-4-6" },
    } as Partial<AppState> as AppState);
  });

  it("labels both roles so neither reads as the global model", async () => {
    render(<SettingsModal />);
    await waitFor(() => expect(screen.getByText(/文本模型 · Text/)).toBeTruthy());
    expect(screen.getByText(/视觉模型 · Vision/)).toBeTruthy();
  });

  it("names which tasks each role serves, since routing is automatic", async () => {
    render(<SettingsModal />);
    await waitFor(() => expect(screen.getByText(/研究问答、配方推荐/)).toBeTruthy());
    expect(screen.getByText(/化学结构图、图表/)).toBeTruthy();
  });

  it("keeps the text save button inside the text section", async () => {
    // It used to sit between the two sections, which is what made the vision
    // block look like an afterthought rather than a peer choice.
    render(<SettingsModal />);
    await waitFor(() => expect(screen.getByText(/文本模型 · Text/)).toBeTruthy());

    const text = topOf("文本模型 · Text");
    const save = topOf("保存并测试连接");
    const vision = topOf("视觉模型 · Vision");
    expect(save).toBeGreaterThan(text);
    expect(save).toBeLessThan(vision);
  });

  it("puts 关闭 after both sections", async () => {
    render(<SettingsModal />);
    await waitFor(() => expect(screen.getByText(/视觉模型 · Vision/)).toBeTruthy());
    const closeBtn = screen.getByRole("button", { name: "关闭" });
    const all = Array.from(document.body.querySelectorAll("*"));
    expect(all.indexOf(closeBtn)).toBeGreaterThan(topOf("视觉模型 · Vision"));
  });

  it("offers the custom endpoint to the text role too", async () => {
    // The provider is generic on purpose — a rented endpoint can serve either
    // role, so it must not be vision-only.
    render(<SettingsModal />);
    await waitFor(() => expect(screen.getByText(/文本模型 · Text/)).toBeTruthy());
    const providerSelects = screen.getAllByRole("combobox");
    const textProvider = providerSelects[0] as HTMLSelectElement;
    const ids = Array.from(textProvider.options).map((o) => o.value);
    expect(ids).toContain("custom");
  });
});
