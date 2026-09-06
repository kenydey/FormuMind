/**
 * P3 wiring smoke: chemical lookup, CitationRenderer (via persisted chat),
 * settings testConnection, Neo4j ensure-schema CTA.
 * Run: node frontend/scripts/p3_smoke.mjs
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const BASE = process.env.FM_BASE || "http://127.0.0.1:5173";
const OUT = "/opt/cursor/artifacts";
await mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.setDefaultTimeout(25000);

try {
  await page.addInitScript(() => {
    const key = "formumind-history";
    const seedHistory = [
      { role: "user", content: "环氧底漆耐盐雾机理？" },
      {
        role: "assistant",
        content:
          "环氧树脂交联后可提供优异附着力与屏障防护[^1]。磷酸锌进一步提升耐盐雾[^2]。\n\n[^1]: CN112345678A — epoxy barrier\n[^2]: US9876543 — zinc phosphate",
        citations: [
          {
            source: "patent",
            identifier: "https://example.com/cn1",
            title: "CN112345678A",
            snippet: "epoxy barrier coating",
            relevance: 0.9,
          },
          {
            source: "patent",
            identifier: "https://example.com/us1",
            title: "US9876543",
            snippet: "zinc phosphate anticorrosion",
            relevance: 0.85,
          },
        ],
      },
    ];
    try {
      const existing = localStorage.getItem(key);
      if (existing) {
        const parsed = JSON.parse(existing);
        parsed.state = { ...(parsed.state || {}), chatHistory: seedHistory };
        localStorage.setItem(key, JSON.stringify(parsed));
      } else {
        localStorage.setItem(
          key,
          JSON.stringify({ state: { chatHistory: seedHistory }, version: 0 }),
        );
      }
    } catch {
      localStorage.setItem(
        key,
        JSON.stringify({ state: { chatHistory: seedHistory }, version: 0 }),
      );
    }
  });

  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  const citeHits = await page.getByText("CN112345678A").count();
  const citeSup = await page.locator(".citation-sup").count();
  console.log("citation_ui_hits=", citeHits, "citation_sup=", citeSup);
  await page.screenshot({ path: `${OUT}/p3-citation-renderer.png`, fullPage: false });

  // Materials — outer ActionsPanel modal + inner MaterialsPanel both use modal-materials
  await page.getByRole("button", { name: /材料库/ }).first().click();
  await page.waitForTimeout(500);
  const mats = page.getByTestId("modal-materials").last();
  await mats.waitFor({ state: "visible" });
  await mats.getByRole("button", { name: /\+ 新材料|新材料/ }).click();
  const edit = page.getByTestId("modal-material-edit");
  await edit.waitFor({ state: "visible" });
  await edit.locator("input").first().fill("ethanol");
  await edit.getByRole("button", { name: /化学查询填充/ }).click();
  await page.waitForTimeout(4000);
  const filled = await edit.getByText(/已自动填充/).count();
  console.log("chemical_lookup_filled=", filled > 0);
  const inputs = edit.locator("input");
  const n = await inputs.count();
  const vals = [];
  for (let i = 0; i < Math.min(n, 8); i++) vals.push(await inputs.nth(i).inputValue());
  console.log("form_values=", JSON.stringify(vals));
  await page.screenshot({ path: `${OUT}/p3-chemical-lookup.png`, fullPage: false });

  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);

  // Settings
  await page.getByTestId("btn-settings").click();
  await page.getByTestId("modal-settings").waitFor({ state: "visible" });
  const onlyTest = page.getByRole("button", { name: /仅测试连接/ });
  console.log("test_connection_btn=", (await onlyTest.count()) > 0);
  if ((await onlyTest.count()) > 0) {
    await onlyTest.click();
    await page.waitForTimeout(3000);
  }
  await page.screenshot({ path: `${OUT}/p3-test-connection.png`, fullPage: false });

  await page.getByRole("button", { name: /依赖管理/ }).click();
  await page.waitForTimeout(700);
  const schemaBtn = page.getByRole("button", { name: /确保 Schema/ });
  console.log("neo4j_schema_btn=", (await schemaBtn.count()) > 0);
  await page.screenshot({ path: `${OUT}/p3-neo4j-schema-btn.png`, fullPage: false });
  if ((await schemaBtn.count()) > 0) {
    await schemaBtn.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${OUT}/p3-neo4j-schema-result.png`, fullPage: false });
  }

  console.log("P3_SMOKE_OK");
} catch (e) {
  console.error("P3_SMOKE_FAIL", e);
  await page.screenshot({ path: `${OUT}/p3-smoke-fail.png`, fullPage: true }).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
