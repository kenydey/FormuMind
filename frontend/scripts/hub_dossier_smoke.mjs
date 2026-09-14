/**
 * Hub dossier / reports UI smoke (P4.6 hand-test companion).
 * Prerequisites: local stack on FM_BASE + dossier/report flags enabled.
 *
 * Run: node frontend/scripts/hub_dossier_smoke.mjs
 * Soft-fail: if flag-gated API returns 409, script exits 0 with WARN (UI still probed).
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const BASE = process.env.FM_BASE || "http://127.0.0.1:5173";
const OUT = "/opt/cursor/artifacts";
await mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.setDefaultTimeout(30000);

let softWarn = 0;

async function shot(name) {
  const path = `${OUT}/${name}.png`;
  await page.screenshot({ path, fullPage: false });
  console.log("saved", path);
}

try {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);

  // Open Knowledge Hub
  const hubBtn = page.getByRole("button", { name: /知识库/ }).first();
  if (!(await hubBtn.count())) {
    // Fallback: Actions panel card
    const card = page.locator("text=知识库").first();
    await card.click();
  } else {
    await hubBtn.click();
  }
  await page.waitForSelector('[data-testid="modal-knowledge-hub"]', { timeout: 15000 });
  await shot("hub-dossier-modal-open");

  // Wiki tab → dossier buttons
  await page.getByTestId("hub-tab-wiki").click();
  await page.waitForSelector('[data-testid="hub-wiki-pane"]');
  await shot("hub-dossier-wiki-pane");

  const openDossier = page.getByTestId("hub-wiki-open-dossier");
  const refreshDossier = page.getByTestId("hub-wiki-refresh-dossier");
  console.log("open dossier disabled?", await openDossier.isDisabled());
  console.log("refresh dossier disabled?", await refreshDossier.isDisabled());

  if (!(await openDossier.isDisabled())) {
    await openDossier.click();
    await page.waitForTimeout(2000);
    const meta = page.getByTestId("hub-wiki-dossier-meta");
    if (await meta.count()) {
      console.log("dossier meta:", ((await meta.textContent()) || "").replace(/\s+/g, " ").slice(0, 160));
      await shot("hub-dossier-reader-meta");
    } else {
      const err = page.locator(".text-rose-300").first();
      const errText = (await err.count()) ? await err.textContent() : "";
      console.log("WARN dossier open — no meta", errText?.slice(0, 200));
      softWarn += 1;
      await shot("hub-dossier-open-warn");
    }

    if (!(await refreshDossier.isDisabled())) {
      await refreshDossier.click();
      await page.waitForTimeout(1500);
      await shot("hub-dossier-after-refresh");
    }
  } else {
    console.log("WARN: 项目卷宗 disabled — select an active project first");
    softWarn += 1;
  }

  // Reports tab → generate briefing (if project active)
  await page.getByTestId("hub-tab-reports").click();
  await page.waitForSelector('[data-testid="hub-reports-pane"]');
  await shot("hub-dossier-reports-pane");
  console.log(
    "reports hint:",
    ((await page.getByTestId("hub-reports-dossier-hint").textContent()) || "").slice(0, 120),
  );

  await page.getByRole("button", { name: /文献简报/ }).click();
  await page.waitForSelector('[data-testid="hub-reports-generate"]');
  const gen = page.getByTestId("hub-reports-generate");
  if (!(await gen.isDisabled())) {
    await gen.click();
    await page.waitForTimeout(2500);
    const disclaimer = page.getByTestId("hub-reports-disclaimer");
    if (await disclaimer.count()) {
      const d = await disclaimer.textContent();
      console.log("disclaimer:", d);
      if (!String(d || "").includes("draft_not_claims")) {
        throw new Error(`expected draft_not_claims, got ${d}`);
      }
      await shot("hub-dossier-report-result");
    } else {
      const err = page.locator(".text-rose-300").first();
      console.log(
        "WARN report generate failed (flags/deps?)",
        (await err.count()) ? await err.textContent() : "",
      );
      softWarn += 1;
      await shot("hub-dossier-report-warn");
    }
  } else {
    console.log("WARN: generate disabled — need active project");
    softWarn += 1;
  }

  console.log(softWarn ? `hub_dossier_smoke done with ${softWarn} soft warning(s)` : "hub_dossier_smoke OK");
  await browser.close();
  process.exit(0);
} catch (e) {
  console.error(e);
  await shot("hub-dossier-smoke-fail").catch(() => {});
  await browser.close();
  process.exit(1);
}
