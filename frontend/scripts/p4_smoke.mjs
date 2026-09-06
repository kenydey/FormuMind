/**
 * P4 wiring smoke: KG calibration, KB search/hybrid probe, experiments search.
 * Run: node frontend/scripts/p4_smoke.mjs
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const BASE = process.env.FM_BASE || "http://127.0.0.1:5173";
const OUT = "/opt/cursor/artifacts";
await mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.setDefaultTimeout(25000);

async function shot(name) {
  const path = `${OUT}/${name}.png`;
  await page.screenshot({ path, fullPage: false });
  console.log("saved", path);
}

try {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);

  // Settings → 依赖管理
  await page.getByTestId("btn-settings").click();
  await page.waitForSelector('[data-testid="modal-settings"]');
  await page.getByRole("button", { name: "依赖管理" }).click();
  await page.waitForTimeout(600);
  await shot("p4-settings-deps");

  // KG calibration
  const calibBtn = page.getByTestId("kg-calibration-btn");
  console.log("calibBtn", await calibBtn.count());
  await calibBtn.click();
  await page.waitForSelector('[data-testid="kg-calibration-panel"]');
  await shot("p4-kg-calibration");
  const calibText = await page.getByTestId("kg-calibration-panel").innerText();
  console.log("calibration:", calibText.replace(/\s+/g, " ").slice(0, 160));

  // KB keyword search
  const probe = page.getByTestId("kb-probe-panel");
  await probe.scrollIntoViewIfNeeded();
  await page.getByTestId("kb-probe-input").fill("epoxy");
  await page.getByTestId("kb-search-btn").click();
  await page.waitForTimeout(1500);
  await shot("p4-kb-search");
  console.log("kb search hits:", await probe.locator(".border.border-edge\\/40").count());

  // KB hybrid
  await page.getByTestId("kb-hybrid-btn").click();
  await page.waitForTimeout(2000);
  await shot("p4-kb-hybrid");
  console.log("kb hybrid panel text:", (await probe.innerText()).replace(/\s+/g, " ").slice(0, 200));

  // Close settings
  const close = page.locator('[data-testid="modal-settings"] button').filter({ hasText: /关闭|✕|×/ }).first();
  if (await close.count()) {
    try {
      await close.click({ timeout: 1000 });
    } catch {
      await page.keyboard.press("Escape");
    }
  } else {
    await page.keyboard.press("Escape");
  }
  await page.waitForTimeout(400);

  // Experiments browser (folded into 实验台账 → 检索/历史)
  await page.getByTestId("open-workbench").click();
  await page.waitForSelector('[data-testid="modal-workbench"]');
  await page.getByTestId("workbench-tab-library").click();
  await page.waitForSelector('[data-testid="experiments-cross-search"]');
  await shot("p4-experiments-modal");

  await page.getByTestId("experiments-search-input").fill("");
  await page.getByTestId("experiments-search-btn").click();
  await page.waitForTimeout(1200);
  const hits = page.getByTestId("experiments-search-hit");
  await hits.first().waitFor({ state: "visible", timeout: 8000 }).catch(() => {});
  console.log("exp search hits", await hits.count());
  await shot("p4-experiments-search");

  console.log("P4 smoke OK");
} catch (e) {
  console.error("P4 smoke FAILED", e);
  await shot("p4-smoke-failure");
  process.exitCode = 1;
} finally {
  await browser.close();
}
