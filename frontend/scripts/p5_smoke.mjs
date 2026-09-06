/**
 * P5 wiring smoke: KG path/retrieve, KB products, Neo4j link CTAs.
 * Run: node frontend/scripts/p5_smoke.mjs
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

  await page.getByTestId("btn-settings").click();
  await page.waitForSelector('[data-testid="modal-settings"]');
  await page.getByRole("button", { name: "依赖管理" }).click();
  await page.waitForTimeout(600);
  await shot("p5-settings-deps");

  const productsInput = page.getByTestId("kb-products-input");
  await productsInput.scrollIntoViewIfNeeded();
  await productsInput.fill("Epon");
  await page.getByTestId("kb-products-btn").click();
  await page.waitForTimeout(1500);
  console.log("product rows", await page.getByTestId("kb-product-row").count());
  await shot("p5-kb-products");

  const probe = page.getByTestId("kb-probe-input");
  await probe.scrollIntoViewIfNeeded();
  await probe.fill("epoxy");
  await page.getByTestId("kg-retrieve-btn").click();
  await page.waitForTimeout(2000);
  console.log(
    "retrieve stats",
    ((await page.getByTestId("kg-retrieve-stats").textContent().catch(() => "")) || "").slice(0, 160)
  );
  await shot("p5-kg-retrieve");

  // Open Neo4j section if collapsed
  const neoStatusBtn = page.getByRole("button", { name: /Neo4j/ }).first();
  if (await neoStatusBtn.count()) {
    await neoStatusBtn.click();
    await page.waitForTimeout(800);
  }
  const linkPanel = page.getByTestId("neo4j-link-panel");
  console.log("neo4j link panel", await linkPanel.count());
  if (await linkPanel.count()) {
    await linkPanel.scrollIntoViewIfNeeded();
    await page.getByTestId("neo4j-link-form-a").fill("form-demo");
    await page.getByTestId("neo4j-link-comp").fill("comp-demo");
    await page.getByTestId("neo4j-link-contains-btn").click();
    await page.waitForTimeout(1000);
    await shot("p5-neo4j-link");
  }

  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);

  const sourceSearch = page.locator('input[placeholder*="检索"], input[placeholder*="搜索"]').first();
  if (await sourceSearch.count()) {
    await sourceSearch.fill("epoxy");
    await page.waitForTimeout(1500);
  }
  const kgToggle = page.getByText("知识图谱关系").first();
  if (await kgToggle.count()) {
    await kgToggle.click();
    await page.waitForTimeout(600);
    const pathPanel = page.getByTestId("kg-path-panel");
    console.log("kg path panel", await pathPanel.count());
    if (await pathPanel.count()) {
      await pathPanel.scrollIntoViewIfNeeded();
      await page.getByTestId("kg-path-src").fill("chem:a");
      await page.getByTestId("kg-path-dst").fill("chem:b");
      await page.getByTestId("kg-path-btn").click();
      await page.waitForTimeout(1000);
      console.log(
        "path result",
        ((await page.getByTestId("kg-path-result").textContent().catch(() => "")) || "").slice(0, 120)
      );
      await shot("p5-kg-path");
    }
  } else {
    console.log("KG relation panel not visible — skip path UI shot");
  }

  console.log("P5 smoke OK");
} catch (e) {
  console.error("P5 smoke FAILED", e);
  await shot("p5-smoke-failure");
  process.exitCode = 1;
} finally {
  await browser.close();
}
