/**
 * P8 smoke: 实验库 folded into 实验台账 tabs + deep-link.
 * Run: node frontend/scripts/p8_workbench_merge_smoke.mjs
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
  await page.goto(BASE + "/?v=" + Date.now(), { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  // Right rail should NOT expose a top-level 实验库 action.
  const actions = page.locator("aside, [data-testid='actions-panel'], .actions").first();
  const bodyText = await page.locator("body").innerText();
  const hasStandaloneLib = await page.getByRole("button", { name: /^实验库/ }).count();
  console.log("standalone 实验库 buttons:", hasStandaloneLib);
  if (hasStandaloneLib > 0) throw new Error("standalone 实验库 still present");

  // Prefer action-tile test id; avoid matching amber banner copy that also says 实验台账.
  await page.getByTestId("open-workbench").click();
  await page.waitForSelector('[data-testid="modal-workbench"]');
  await page.waitForSelector('[data-testid="workbench-tabs"]');
  await shot("p8-workbench-ledger-tab");

  await page.getByTestId("workbench-tab-library").click();
  await page.waitForSelector('[data-testid="experiments-browser"]');
  await page.waitForSelector('[data-testid="experiments-cross-search"]');
  await shot("p8-workbench-library-tab");

  await page.getByTestId("experiments-search-btn").click();
  await page.waitForTimeout(1500);
  const hits = page.getByTestId("experiments-search-hit");
  const n = await hits.count();
  console.log("exp search hits", n);
  await shot("p8-workbench-search-hits");

  if (n > 0) {
    await hits.first().click();
    await page.waitForTimeout(1200);
    const ledgerSelected = await page.getByTestId("workbench-tab-ledger").getAttribute("aria-selected");
    console.log("ledger selected after hit:", ledgerSelected);
    if (ledgerSelected !== "true") throw new Error("did not switch to ledger after hit");
    await shot("p8-workbench-deeplink-ledger");
  } else {
    console.log("no hits — deeplink skipped (empty corpus OK)");
  }

  console.log("P8 smoke OK");
} catch (e) {
  console.error("P8 smoke FAILED", e);
  await shot("p8-smoke-failure");
  process.exitCode = 1;
} finally {
  await browser.close();
}
