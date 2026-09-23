/**
 * Headless proof for G5/G6 KG measured observability UI.
 * Usage: node scripts/kg_measured_obs_ui_proof.mjs
 */
import { chromium } from "playwright";
import fs from "fs";
import path from "path";

const BASE = process.env.FM_BASE || "http://127.0.0.1:5173";
const OUT = "/opt/cursor/artifacts";
fs.mkdirSync(OUT, { recursive: true });

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH || "/usr/local/bin/google-chrome",
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const context = await browser.newContext({
    viewport: { width: 1400, height: 900 },
    recordVideo: { dir: OUT, size: { width: 1400, height: 900 } },
  });
  const page = await context.newPage();
  const log = [];
  const step = (m) => {
    log.push(m);
    console.log(m);
  };

  try {
    // Ensure a project exists for Hub
    const created = await page.request.post(`${BASE}/api/projects`, {
      data: {
        title: "KG可观测手测",
        requirement: {
          domain: "anticorrosion_coating",
          substrate: "carbon_steel",
          salt_spray_hours: 720,
          voc_limit_gpl: 350,
        },
      },
    });
    step(`create project status=${created.status()}`);

    await page.goto(BASE, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(1500);

    // Open Knowledge Hub via Actions 知识库
    const knowledgeBtn = page.getByTestId("open-knowledge");
    await knowledgeBtn.click({ timeout: 15000 });
    await page.getByTestId("modal-knowledge-hub").waitFor({ timeout: 15000 });
    step("opened knowledge hub");

    // Graph tab
    await page.getByTestId("hub-tab-graph").click();
    await page.getByTestId("hub-graph-pane").waitFor({ timeout: 15000 });
    step("opened graph pane");

    // Wait for stats strip (compact hides when 0 — our API has measured_total>=1)
    const strip = page.getByTestId("kg-feedback-stats-strip");
    await strip.waitFor({ timeout: 20000 });
    const stripText = (await strip.textContent()) || "";
    step(`hub strip: ${stripText}`);
    await page.waitForTimeout(1500);
    await page.screenshot({
      path: path.join(OUT, "kg-obs-hub-graph-stats-strip.png"),
      fullPage: false,
    });
    step("saved kg-obs-hub-graph-stats-strip.png");

    // Highlight strip region
    await strip.evaluate((el) => {
      el.style.outline = "2px solid #fbbf24";
      el.style.outlineOffset = "2px";
    });
    await page.waitForTimeout(800);
    await strip.screenshot({
      path: path.join(OUT, "kg-obs-hub-stats-strip-closeup.png"),
    });
    step("saved kg-obs-hub-stats-strip-closeup.png");

    fs.writeFileSync(path.join(OUT, "kg_measured_obs_ui_proof.log"), log.join("\n") + "\n");
    console.log("OK");
  } finally {
    const vid = page.video();
    await context.close();
    await browser.close();
    if (vid) {
      const vpath = await vid.path();
      const dest = path.join(OUT, "kg-obs-hub-graph-stats-strip.webm");
      try {
        fs.renameSync(vpath, dest);
        console.log("saved", dest);
      } catch (e) {
        console.log("video path", vpath, e);
      }
    }
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
