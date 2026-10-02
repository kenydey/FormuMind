/**
 * Wave 0 (round-3): 设置页从 11 tab 收敛到 4 tab。
 * 2026-10-02: 「高级」拆分为「环境变量」(env) 与「依赖管理」(deps) 两个 tab。
 *
 * 旧 tab id（可能已持久化在用户 localStorage）在这里做一次性映射，
 * 升级后不白屏、不出现空 tab。
 */
export const SETTINGS_TABS = ["model", "capabilities", "prefs", "env", "deps"] as const;

export type SettingsTab = (typeof SETTINGS_TABS)[number];

/** 旧 id → 新 id。settingsTab 之外的去向（memory/project/org）由 openSettings 特殊处理。 */
const LEGACY_TAB_MAP: Record<string, SettingsTab> = {
  llm: "model",
  api: "model",
  skills: "capabilities",
  connectors: "capabilities",
  notebooklm: "capabilities",
  recommend: "prefs",
  advanced: "env",
};

export function normalizeSettingsTab(id: string | null | undefined): SettingsTab {
  if (!id) return "model";
  if ((SETTINGS_TABS as readonly string[]).includes(id)) return id as SettingsTab;
  return LEGACY_TAB_MAP[id] ?? "model";
}

/** 不再属于设置页的旧 id（记忆→知识库，项目→历史面板，看板→独立入口）。 */
export function isRelocatedSettingsTab(id: string | null | undefined): id is "memory" | "project" | "org" {
  return id === "memory" || id === "project" || id === "org";
}
