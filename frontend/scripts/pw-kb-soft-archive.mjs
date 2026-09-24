/**
 * Walkthrough: Hub KB soft-archive (W3).
 * Run from frontend/: node scripts/pw-kb-soft-archive.mjs
 */
import { chromium } from "playwright";
import path from "node:path";
import fs from "node:fs";

const OUT = "/opt/cursor/artifacts";
const PID = "970df409-5d16-474b-a84b-e15ea10b571f";
const SID = "c6a77975-68aa-40cb-a8f7-feea06ba7e1d";
fs.mkdirSync(OUT, { recursive: true });

async function api(method, url, body) {
  const r = await fetch(`http://127.0.0.1:8000${url}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    json = text;
  }
  if (!r.ok) throw new Error(`${method} ${url} → ${r.status} ${text}`);
  return json;
}

// Ensure source is active before UI demo
await api("POST", `/api/kb/sources/${SID}/archive`, { archived: false });

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
  await page.waitForTimeout(1200);

  const opened = await page.evaluate(
    ({ pid }) => {
      const store = window.__FM_STORE__;
      if (!store?.setState) return null;
      store.setState({ activeProjectId: pid, openModal: "knowledge", knowledgeHubTab: "materials" });
      return "store";
    },
    { pid: PID },
  );
  if (!opened) throw new Error("no __FM_STORE__");

  await page.waitForSelector('[data-testid="hub-materials-pane"]', { timeout: 15000 });
  await page.waitForSelector(`[data-testid="hub-materials-archive-${SID}"]`, { timeout: 15000 });

  // Accept confirm dialog
  page.once("dialog", (d) => d.accept());
  await page.getByTestId(`hub-materials-archive-${SID}`).click();
  await page.waitForTimeout(800);

  // Row should disappear from default list
  await page.waitForSelector(`[data-testid="hub-materials-archive-${SID}"]`, {
    state: "detached",
    timeout: 10000,
  });
  await page.screenshot({ path: path.join(OUT, "kb_soft_archive_hidden.png") });

  // Show archived
  await page.locator('[data-testid="hub-materials-include-archived"] input').check();
  await page.waitForSelector(`[data-testid="hub-materials-archive-${SID}"]`, { timeout: 10000 });
  const label = await page.getByTestId(`hub-materials-archive-${SID}`).innerText();
  if (!label.includes("恢复")) throw new Error(`expected 恢复, got ${label}`);
  await page.screenshot({ path: path.join(OUT, "kb_soft_archive_listed.png") });

  // Restore
  await page.getByTestId(`hub-materials-archive-${SID}`).click();
  await page.waitForTimeout(600);
  await page.locator('[data-testid="hub-materials-include-archived"] input').uncheck();
  await page.waitForSelector(`[data-testid="hub-materials-archive-${SID}"]`, { timeout: 10000 });
  const again = await page.getByTestId(`hub-materials-archive-${SID}`).innerText();
  if (!again.includes("归档")) throw new Error(`expected 归档 after restore, got ${again}`);
  await page.screenshot({ path: path.join(OUT, "kb_soft_archive_restored.png") });

  console.log("PASS: KB soft-archive Hub UI");
} catch (err) {
  await page.screenshot({ path: path.join(OUT, "kb_soft_archive_FAIL.png") }).catch(() => {});
  console.error("FAIL:", err);
  process.exitCode = 1;
} finally {
  const vid = page.video();
  await context.close();
  await browser.close();
  if (vid) {
    const vpath = await vid.path();
    const dest = path.join(OUT, "kb_soft_archive_hub_walkthrough.webm");
    fs.renameSync(vpath, dest);
    console.log("VIDEO:", dest);
  }
}
