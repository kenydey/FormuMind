/**
 * Hub UI evidence for golden R&D project (dossier five-tables + S4 drafts + Report).
 * Usage:
 *   FM_PROJECT_ID=<uuid> node scripts/golden_hub_dossier_ui_smoke.mjs
 *   (optional) runs after scripts/golden_rd_loop_smoke.py
 */
import { chromium } from "../frontend/node_modules/playwright/index.mjs";
import { mkdir, writeFile } from "node:fs/promises";

const BASE = process.env.FM_BASE || "http://127.0.0.1:5173";
const PID = (process.env.FM_PROJECT_ID || "").trim();
const OUT = "/opt/cursor/artifacts";
await mkdir(OUT, { recursive: true });

if (!PID) {
  console.error("FM_PROJECT_ID required");
  process.exit(2);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
page.setDefaultTimeout(45000);

async function shot(name) {
  const path = `${OUT}/${name}.png`;
  await page.screenshot({ path, fullPage: false });
  console.log("saved", path);
  return path;
}

try {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  // Inject active project into zustand store (expose via window if available).
  const injected = await page.evaluate((pid) => {
    const store = window.__FM_STORE__ || window.useStore || null;
    // Try common FormuMind patterns
    const candidates = [
      () => window.__ZUSTAND_STORE__?.setState?.({ activeProjectId: pid }),
      () => {
        const el = document.querySelector("[data-fm-store]");
        return false;
      },
    ];
    // Direct: look for store on window from Vite HMR / debug
    if (window.__FM_DEBUG_STORE__?.setState) {
      window.__FM_DEBUG_STORE__.setState({ activeProjectId: pid });
      return "debug_store";
    }
    // Fallback: localStorage keys used by persistence
    try {
      for (const k of Object.keys(localStorage)) {
        if (/project|formumind|fm-/i.test(k)) {
          // leave for logging
        }
      }
    } catch {}
    return store ? "store_found" : "no_store";
  }, PID);
  console.log("inject attempt:", injected);

  // Prefer UI: open project picker / set topic if present
  // Always open Knowledge Hub first (scroll into view without fragile panel scroll)
  const hubBtn = page.getByTestId("open-knowledge");
  if (await hubBtn.count()) {
    await hubBtn.evaluate((el) => el.scrollIntoView({ block: "center" }));
    await hubBtn.click({ force: true });
  } else {
    await page.getByRole("button", { name: /知识库/ }).first().click({ force: true });
  }
  await page.waitForSelector('[data-testid="modal-knowledge-hub"]', { timeout: 20000 });
  await shot("golden-hub-modal-open");

  // Set active project via store after modules load — import from page context
  await page.evaluate(async (pid) => {
    // Dynamic import of the app store module (Vite)
    const mods = Object.keys(window).filter((k) => k.includes("Store"));
    // Try clicking any project list row matching pid prefix
    const text = document.body.innerText || "";
    return { mods, hasPid: text.includes(pid.slice(0, 8)) };
  }, PID);

  // Use API in-page to ensure dossier flags, then open Wiki tab
  await page.getByTestId("hub-tab-wiki").click();
  await page.waitForSelector('[data-testid="hub-wiki-pane"]');

  // Force-set zustand via vite module graph if exposed
  const setOk = await page.evaluate(async (pid) => {
    try {
      // Prefer global set by App
      if (typeof window.__setActiveProjectId === "function") {
        window.__setActiveProjectId(pid);
        return "fn";
      }
      const mod = await import("/src/store.ts");
      const useStore = mod.useStore || mod.default;
      if (useStore?.setState) {
        useStore.setState({ activeProjectId: pid });
        return "store.ts";
      }
    } catch (e) {
      return String(e);
    }
    try {
      const mod = await import("/src/store/index.ts");
      const useStore = mod.useStore || mod.default;
      if (useStore?.setState) {
        useStore.setState({ activeProjectId: pid });
        return "store/index.ts";
      }
    } catch (e) {
      return "fail:" + String(e);
    }
    return "unset";
  }, PID);
  console.log("setActiveProjectId:", setOk);
  await page.waitForTimeout(500);

  const scope = page.getByTestId("hub-wiki-project-scope");
  const scopeText = (await scope.count()) ? await scope.textContent() : "";
  console.log("wiki scope:", (scopeText || "").replace(/\s+/g, " ").slice(0, 120));
  await shot("golden-hub-wiki-scope");

  const openDossier = page.getByTestId("hub-wiki-open-dossier");
  if (await openDossier.isDisabled()) {
    console.log("WARN: dossier still disabled — project not active in UI");
    await shot("golden-hub-dossier-disabled");
  } else {
    await openDossier.click();
    await page.waitForTimeout(2500);
    const meta = page.getByTestId("hub-wiki-dossier-meta");
    if (await meta.count()) {
      console.log("meta:", ((await meta.textContent()) || "").replace(/\s+/g, " ").slice(0, 200));
    }
    await shot("golden-hub-dossier-open");

    // Scroll reader to S1 / S8 if present in modal
    const reader = page.locator('[data-testid="hub-wiki-reader"], .prose, article').first();
    if (await reader.count()) {
      await reader.evaluate((el) => {
        const s1 = el.querySelector("h2,h3") || el;
        s1?.scrollIntoView?.({ block: "start" });
      });
      await shot("golden-hub-dossier-s1");
      await reader.evaluate((el) => {
        const nodes = [...el.querySelectorAll("h2,h3")];
        const s8 = nodes.find((n) => /S8|开放问题/.test(n.textContent || ""));
        (s8 || nodes[nodes.length - 1])?.scrollIntoView?.({ block: "start" });
      });
      await page.waitForTimeout(400);
      await shot("golden-hub-dossier-s8");
    }

    const refresh = page.getByTestId("hub-wiki-refresh-dossier");
    if (!(await refresh.isDisabled())) {
      await refresh.click();
      await page.waitForTimeout(1500);
      await shot("golden-hub-dossier-refreshed");
    }
  }

  await page.getByTestId("hub-tab-reports").click();
  await page.waitForSelector('[data-testid="hub-reports-pane"]');
  await shot("golden-hub-reports-pane");
  // Select briefing template card then generate
  const briefing = page.getByRole("button", { name: /文献简报/ }).first();
  if (await briefing.count()) {
    await briefing.click();
    await page.waitForTimeout(400);
  }
  const gen = page.getByTestId("hub-reports-generate");
  console.log("generate disabled?", await gen.isDisabled().catch(() => true));
  if ((await gen.count()) && !(await gen.isDisabled())) {
    await gen.click();
    await page.waitForTimeout(3000);
    await shot("golden-hub-report-briefing");
    const disc = page.getByTestId("hub-reports-disclaimer");
    if (await disc.count()) {
      console.log("disclaimer:", await disc.textContent());
    }
    const exportMd = page.getByTestId("hub-reports-export-md");
    if ((await exportMd.count()) && !(await exportMd.isDisabled())) {
      const [download] = await Promise.all([
        page.waitForEvent("download", { timeout: 20000 }),
        exportMd.click(),
      ]);
      const savePath = `${OUT}/golden-hub-report-export.md`;
      await download.saveAs(savePath);
      console.log("saved", savePath, download.suggestedFilename());
      await shot("golden-hub-report-export-md");
    }
  } else {
    console.log("WARN: report generate disabled");
  }

  // Back to Wiki: open S4 query draft + dossier S8 text via API into artifact
  await page.getByTestId("hub-tab-wiki").click();
  const draftRow = page.locator("text=硅烷 pH 窗口笔记").first();
  if (await draftRow.count()) {
    await draftRow.click();
    await page.waitForTimeout(800);
    await shot("golden-hub-s4-query-draft");
  }

  await writeFile(
    `${OUT}/golden_hub_ui_smoke.txt`,
    `project_id=${PID}\nsetActive=${setOk}\nscope=${scopeText}\n`,
  );
  console.log("golden_hub_dossier_ui_smoke done");
  await browser.close();
  process.exit(0);
} catch (e) {
  console.error(e);
  await shot("golden-hub-ui-fail").catch(() => {});
  await browser.close();
  process.exit(1);
}
