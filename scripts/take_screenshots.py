"""Generate updated screenshots for FormuMind docs (v0.7 state)."""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from _ui import click_if_present, close_modal, in_modal, open_modal  # noqa: E402

IMAGES = Path(__file__).parent.parent / "docs" / "images"
BASE = "http://localhost:5173"


async def shot(page, path: str):
    await asyncio.sleep(0.9)
    await page.screenshot(path=str(IMAGES / path), full_page=False)
    print(f"  ✓ {path}")


async def main():
    IMAGES.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        # ── 01 Overview ────────────────────────────────────────────────────────
        print("01-overview…")
        await page.goto(BASE, wait_until="networkidle")
        await asyncio.sleep(2)
        await shot(page, "01-overview.png")

        # ── 02 Search / Sources ─────────────────────────────────────────────────
        print("02-search…")
        await page.locator("textarea").first.fill("低 VOC 水性防腐蚀涂料配方研究")
        await asyncio.sleep(0.3)
        # Toggle Internet + NotebookLM on
        await page.locator("button:has-text('互联网')").click()
        await asyncio.sleep(0.2)
        await page.locator("button:has-text('NotebookLM')").click()
        await asyncio.sleep(0.2)
        await shot(page, "02-search.png")

        # ── Trigger search to load some sources ─────────────────────────────────
        print("  searching for sources…")
        await page.locator('[data-testid="btn-search"]').click()
        await asyncio.sleep(6)

        # ── 03 Research Q&A ─────────────────────────────────────────────────────
        print("03-research…")
        # The chat input should now be enabled
        enabled_inputs = page.locator("input:not([disabled]), textarea:not([disabled])")
        cnt = await enabled_inputs.count()
        # Try to fill the last enabled input (the chat input)
        for i in range(cnt - 1, -1, -1):
            el = enabled_inputs.nth(i)
            ph = (await el.get_attribute("placeholder") or "").lower()
            if "加载" not in ph and "研究主题" not in ph:
                try:
                    await el.fill("这些专利中防腐蚀的主要机理是什么？", timeout=3000)
                    break
                except Exception:
                    pass
        await shot(page, "03-research.png")

        # ── 04 Settings modal ──────────────────────────────────────────────────
        print("04-settings…")
        await page.locator('[data-testid="btn-settings"]').click()
        await asyncio.sleep(1)
        await shot(page, "04-settings.png")
        await close_modal(page, "settings")

        # ── 05 Recommend modal ─────────────────────────────────────────────────
        print("05-recommend…")
        await open_modal(page, "recommend")
        inner = in_modal(page, "recommend", "button").filter(has_text="检索专利并推荐")
        if not await inner.count():
            inner = in_modal(page, "recommend", "button").filter(has_text="推荐")
        if not await click_if_present(inner, settle=8):
            print("  ! recommend action button not found — capturing as-is")
        await shot(page, "05-recommend.png")
        await close_modal(page, "recommend")

        # ── 06 DOE modal ────────────────────────────────────────────────────────
        print("06-doe…")
        await open_modal(page, "doe")
        # Select AI active selection
        sel = in_modal(page, "doe", "select")
        if await sel.count():
            opts = await sel.first.evaluate("el => [...el.options].map(o=>o.value)")
            # Pick the AI option (active/lhs usually last option)
            ai_opts = [o for o in opts if "active" in o.lower() or "ai" in o.lower() or "lhs" in o.lower()]
            if ai_opts:
                await sel.first.select_option(value=ai_opts[-1])
                await asyncio.sleep(0.3)
        # Click generate button inside modal
        gen = in_modal(page, "doe", "button").filter(has_text="生成")
        if not await click_if_present(gen, settle=4):
            print("  ! DOE generate button not found — capturing as-is")
        await shot(page, "06-doe.png")
        await close_modal(page, "doe")

        # ── 07 Optimize / convergence modal ────────────────────────────────────
        print("07-optimize…")
        await open_modal(page, "optimize")
        run_btn = in_modal(page, "optimize", "button").filter(has_text="运行")
        if not await click_if_present(run_btn, settle=9):
            print("  ! optimize run button not found — capturing as-is")
        await shot(page, "07-optimize.png")
        await close_modal(page, "optimize")

        await browser.close()

    print("\nAll screenshots saved to docs/images/")


asyncio.run(main())
