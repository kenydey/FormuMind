/**
 * W5 walkthrough: LoopModal retry banner + auto-adopt checkbox.
 * Run: cd frontend && node scripts/pw-loop-retry-adopt.mjs
 */
import { chromium } from "playwright";
import path from "node:path";
import fs from "node:fs";

const OUT = "/opt/cursor/artifacts";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROME || "/usr/local/bin/google-chrome",
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const context = await browser.newContext({
  viewport: { width: 1400, height: 900 },
  recordVideo: { dir: OUT, size: { width: 1400, height: 900 } },
});
const page = await context.newPage();

try {
  await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(1000);

  const ok = await page.evaluate(() => {
    const store = window.__FM_STORE__;
    if (!store?.setState) return false;
    store.setState({
      openModal: "loop",
      busy: "idle",
      error: "模拟：闭环任务失败（broker down）",
      loopRetryAvailable: true,
      lastLoopTaskId: "loop-demo",
      autoAdoptNextDoeOnLoop: false,
      workbenchCampaignId: 1,
      loopReport: null,
    });
    return true;
  });
  if (!ok) throw new Error("no __FM_STORE__");

  await page.waitForSelector('[data-testid="loop-retry-banner"]', { timeout: 10000 });
  await page.waitForSelector('[data-testid="loop-auto-adopt"]', { timeout: 5000 });
  await page.screenshot({ path: path.join(OUT, "loop_retry_banner.png") });

  await page.locator('[data-testid="loop-auto-adopt"] input').check();
  await page.waitForTimeout(200);
  const checked = await page.locator('[data-testid="loop-auto-adopt"] input').isChecked();
  if (!checked) throw new Error("auto-adopt checkbox not checked");
  await page.screenshot({ path: path.join(OUT, "loop_auto_adopt_checked.png") });

  console.log("PASS: W5 loop retry / auto-adopt UI");
} catch (err) {
  await page.screenshot({ path: path.join(OUT, "loop_w5_FAIL.png") }).catch(() => {});
  console.error("FAIL:", err);
  process.exitCode = 1;
} finally {
  const vid = page.video();
  await context.close();
  await browser.close();
  if (vid) {
    const vpath = await vid.path();
    const dest = path.join(OUT, "loop_retry_adopt_walkthrough.webm");
    fs.renameSync(vpath, dest);
    console.log("VIDEO:", dest);
  }
}
