/**
 * The vision-model section of the settings page.
 *
 * The behaviours pinned here are the ones a careless refactor would break
 * invisibly: that "follow the text model" sends an *empty* provider (the wire
 * value that means inherit), that a custom endpoint gets a free-text model field
 * rather than a dropdown that cannot express `tgi`, and that "测试视觉" is the
 * only thing allowed to report capability — because the server deliberately
 * refuses to guess it.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type LLMProviderInfo, type VisionSettings } from "../api";
import VisionModelPanel from "./VisionModelPanel";

const PROVIDERS: LLMProviderInfo[] = [
  {
    id: "openai",
    label: "OpenAI",
    models: [
      { id: "gpt-4o-mini", label: "gpt-4o-mini" },
      { id: "gpt-4o", label: "gpt-4o", recommended: true },
    ],
  },
  { id: "deepseek", label: "DeepSeek", base_url: "https://api.deepseek.com", models: [
    { id: "deepseek-v4-pro", label: "deepseek-v4-pro", recommended: true },
  ] },
  { id: "custom", label: "OpenAI 兼容自定义端点", base_url: null, models: [] },
];

const inheriting = (over: Partial<VisionSettings> = {}): VisionSettings => ({
  provider: "",
  model: "deepseek-v4-pro",
  base_url: null,
  key_set: true,
  inherits: true,
  configured: false,
  hint: "deepseek 不支持图片输入",
  ...over,
});

function renderPanel(vision: VisionSettings | null, onSaved = vi.fn()) {
  return {
    onSaved,
    ...render(<VisionModelPanel providers={PROVIDERS} vision={vision} onSaved={onSaved} />),
  };
}

describe("VisionModelPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "postVisionSettings").mockResolvedValue({
      ok: true,
      provider: "",
      model: "",
      base_url: null,
      key_set: false,
      inherits: true,
      configured: false,
      hint: "",
    });
    vi.spyOn(api, "testVision").mockResolvedValue({ ok: true, message: "视觉模型可用（1.2s）：OK" });
    vi.spyOn(api, "refreshLlmModels").mockResolvedValue({
      ok: true,
      provider: "custom",
      base_url: "https://x.endpoints.huggingface.cloud/v1",
      source: "remote",
      models: [{ id: "tgi", label: "tgi" }],
      message: "已从远端获取 1 个模型",
    });
  });

  it("defaults to following the text model and says which one", () => {
    renderPanel(inheriting());
    const select = screen.getByLabelText("视觉模型供应商") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(screen.getByText(/与文本模型共用同一供应商与密钥/)).toBeTruthy();
    expect(screen.getByText("deepseek-v4-pro")).toBeTruthy();
  });

  it("surfaces the server hint when vision cannot work", () => {
    // The whole point: DeepSeek for text silently killed figures. Now it says so.
    renderPanel(inheriting({ configured: false, hint: "deepseek 不支持图片输入" }));
    expect(screen.getByText(/deepseek 不支持图片输入/)).toBeTruthy();
  });

  it("hides the per-role fields while inheriting", () => {
    renderPanel(inheriting());
    expect(screen.queryByLabelText("视觉模型名称")).toBeNull();
    expect(screen.queryByLabelText("视觉模型 API Key")).toBeNull();
  });

  it("sends an empty provider when set back to following the text model", async () => {
    renderPanel(inheriting({ provider: "openai", model: "gpt-4o", inherits: false, configured: true, hint: "" }));
    await userEvent.selectOptions(screen.getByLabelText("视觉模型供应商"), "");
    await userEvent.click(screen.getByRole("button", { name: "保存视觉设置" }));

    await waitFor(() => expect(api.postVisionSettings).toHaveBeenCalled());
    // Empty string is the wire value meaning "inherit" — undefined would leave
    // the previous provider in place, which is the opposite of what was asked.
    expect(api.postVisionSettings).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "", model: "" })
    );
  });

  it("saves a separate provider without touching the text role", async () => {
    const { onSaved } = renderPanel(inheriting());
    await userEvent.selectOptions(screen.getByLabelText("视觉模型供应商"), "openai");
    await userEvent.click(screen.getByRole("button", { name: "保存视觉设置" }));

    await waitFor(() => expect(api.postVisionSettings).toHaveBeenCalled());
    expect(api.postVisionSettings).toHaveBeenCalledWith(
      // Auto-picks the recommended model so the field is never left blank.
      expect.objectContaining({ provider: "openai", model: "gpt-4o" })
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it("uses a free-text model field for a custom endpoint", async () => {
    // A dropdown cannot express TGI's literal "tgi", nor an arbitrary repo id.
    renderPanel(inheriting());
    await userEvent.selectOptions(screen.getByLabelText("视觉模型供应商"), "custom");

    const field = screen.getByLabelText("视觉模型名称");
    expect(field.tagName).toBe("INPUT");
    expect(screen.getByLabelText("视觉端点 Base URL")).toBeTruthy();
  });

  it("uses a dropdown for a catalogued provider", async () => {
    renderPanel(inheriting());
    await userEvent.selectOptions(screen.getByLabelText("视觉模型供应商"), "openai");
    expect(screen.getByLabelText("视觉模型名称").tagName).toBe("SELECT");
    // No Base URL box for a provider whose endpoint we already know.
    expect(screen.queryByLabelText("视觉端点 Base URL")).toBeNull();
  });

  it("saves a custom endpoint with its model, url and token", async () => {
    renderPanel(inheriting());
    await userEvent.selectOptions(screen.getByLabelText("视觉模型供应商"), "custom");
    await userEvent.type(screen.getByLabelText("视觉模型名称"), "tgi");
    await userEvent.type(
      screen.getByLabelText("视觉端点 Base URL"),
      "https://vlm.endpoints.huggingface.cloud"
    );
    await userEvent.type(screen.getByLabelText("视觉模型 API Key"), "hf_token");
    await userEvent.click(screen.getByRole("button", { name: "保存视觉设置" }));

    await waitFor(() => expect(api.postVisionSettings).toHaveBeenCalled());
    expect(api.postVisionSettings).toHaveBeenCalledWith({
      provider: "custom",
      model: "tgi",
      api_key: "hf_token",
      baseUrl: "https://vlm.endpoints.huggingface.cloud",
    });
  });

  it("discovers the model name from the endpoint", async () => {
    renderPanel(inheriting());
    await userEvent.selectOptions(screen.getByLabelText("视觉模型供应商"), "custom");
    await userEvent.click(screen.getByRole("button", { name: "更新视觉模型列表" }));

    await waitFor(() => expect(api.refreshLlmModels).toHaveBeenCalled());
    expect(screen.getByText(/已从远端获取 1 个模型/)).toBeTruthy();
    expect((screen.getByLabelText("视觉模型名称") as HTMLInputElement).value).toBe("tgi");
  });

  it("reports the probe result", async () => {
    renderPanel(inheriting({ configured: true, hint: "" }));
    await userEvent.click(screen.getByRole("button", { name: "测试视觉" }));
    await waitFor(() => expect(screen.getByText(/视觉模型可用/)).toBeTruthy());
  });

  it("reports a cold endpoint as such rather than as broken", async () => {
    vi.spyOn(api, "testVision").mockResolvedValue({
      ok: false,
      message: "视觉端点正在冷启动（503），副本就绪后重试即可",
    });
    renderPanel(inheriting({ configured: true, hint: "" }));
    await userEvent.click(screen.getByRole("button", { name: "测试视觉" }));
    await waitFor(() => expect(screen.getByText(/冷启动/)).toBeTruthy());
  });

  it("cannot probe an unconfigured role", () => {
    renderPanel(inheriting({ configured: false }));
    expect(screen.getByRole("button", { name: "测试视觉" })).toBeDisabled();
  });

  it("saving alone never claims the model can see", async () => {
    // Only the probe may assert capability — the save path must not imply it.
    renderPanel(inheriting());
    await userEvent.selectOptions(screen.getByLabelText("视觉模型供应商"), "custom");
    await userEvent.type(screen.getByLabelText("视觉模型名称"), "tgi");
    await userEvent.click(screen.getByRole("button", { name: "保存视觉设置" }));

    await waitFor(() => expect(screen.getByText(/已保存（点/)).toBeTruthy());
    // The save banner points at the real check rather than implying it passed.
    expect(screen.getByText(/验证是否真的能读图/)).toBeTruthy();
    expect(screen.queryByText(/视觉模型可用/)).toBeNull();
  });

  it("keeps the confirmation visible after the parent refetches", async () => {
    // Saving calls onSaved(), which reloads /api/settings and pushes a new
    // `vision` prop straight back down. Syncing that must not wipe the banner,
    // or a successful save looks like nothing happened — which is exactly how it
    // behaved when first wired up.
    const { rerender } = renderPanel(inheriting());
    await userEvent.selectOptions(screen.getByLabelText("视觉模型供应商"), "openai");
    await userEvent.click(screen.getByRole("button", { name: "保存视觉设置" }));
    await waitFor(() => expect(screen.getByText(/已保存（点/)).toBeTruthy());

    rerender(
      <VisionModelPanel
        providers={PROVIDERS}
        vision={inheriting({ provider: "openai", model: "gpt-4o", inherits: false, configured: true, hint: "" })}
        onSaved={vi.fn()}
      />
    );
    expect(screen.getByText(/已保存（点/)).toBeTruthy();
  });

  it("shows the save failure instead of a silent no-op", async () => {
    vi.spyOn(api, "postVisionSettings").mockRejectedValue(new Error("422 base_url"));
    renderPanel(inheriting());
    await userEvent.selectOptions(screen.getByLabelText("视觉模型供应商"), "custom");
    await userEvent.click(screen.getByRole("button", { name: "保存视觉设置" }));
    await waitFor(() => expect(screen.getByText(/422 base_url/)).toBeTruthy());
  });
});
