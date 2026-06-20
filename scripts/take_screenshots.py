"""Generate updated screenshots for FormuMind docs (v0.7 state)."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

IMAGES = Path(__file__).parent.parent / "docs" / "images"
BASE = "http://localhost:5173"


async def shot(page, path: str):
    await asyncio.sleep(0.9)
    await page.screenshot(path=str(IMAGES / path), full_page=False)
    print(f"  ✓ {path}")


async def close_modal(page):
    """Close modal by clicking its × button or clicking the top-left backdrop corner."""
    # Try the × close button first
    close_x = page.locator(".fixed.inset-0.z-50 button").filter(has_text="×")
    if await close_x.count():
        await close_x.click()
        await asyncio.sleep(0.5)
        return
    # Click backdrop corner (outside the centered modal content)
    await page.mouse.click(10, 10)
    await asyncio.sleep(0.5)
    # Final fallback
    if await page.locator(".fixed.inset-0.z-50").count():
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)


async def click_inside_modal(page, selector: str):
    """Click a button inside the open modal."""
    return await page.locator(f".fixed.inset-0.z-50 {selector}").first.click()


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
        await page.locator("button:has-text('开始检索')").click()
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
        await page.locator("button:has-text('设置')").first.click()
        await asyncio.sleep(1)
        await shot(page, "04-settings.png")
        await close_modal(page)

        # ── 05 Recommend modal ─────────────────────────────────────────────────
        print("05-recommend…")
        await page.locator("button:has-text('推荐配方')").first.click()
        await asyncio.sleep(1)
        # Click the action button inside the modal
        inner = page.locator(".fixed.inset-0.z-50 button").filter(has_text="检索专利并推荐")
        if not await inner.count():
            inner = page.locator(".fixed.inset-0.z-50 button").filter(has_text="推荐")
        if await inner.count():
            await inner.first.click()
            await asyncio.sleep(8)
        await shot(page, "05-recommend.png")
        await close_modal(page)

        # ── 06 DOE modal ────────────────────────────────────────────────────────
        print("06-doe…")
        await page.locator("button:has-text('DOE 设计')").first.click()
        await asyncio.sleep(1)
        # Select AI active selection
        sel = page.locator(".fixed.inset-0.z-50 select")
        if await sel.count():
            opts = await sel.first.evaluate("el => [...el.options].map(o=>o.value)")
            # Pick the AI option (active/lhs usually last option)
            ai_opts = [o for o in opts if "active" in o.lower() or "ai" in o.lower() or "lhs" in o.lower()]
            if ai_opts:
                await sel.first.select_option(value=ai_opts[-1])
                await asyncio.sleep(0.3)
        # Click generate button inside modal
        gen = page.locator(".fixed.inset-0.z-50 button").filter(has_text="生成")
        if await gen.count():
            await gen.first.click()
            await asyncio.sleep(4)
        await shot(page, "06-doe.png")
        await close_modal(page)

        # ── 07 Optimize / convergence modal ────────────────────────────────────
        print("07-optimize…")
        await page.locator("button:has-text('寻优收敛')").first.click()
        await asyncio.sleep(1)
        run_btn = page.locator(".fixed.inset-0.z-50 button").filter(has_text="运行")
        if await run_btn.count():
            await run_btn.first.click()
            await asyncio.sleep(9)
        await shot(page, "07-optimize.png")
        await close_modal(page)

        await browser.close()

    print("\nAll screenshots saved to docs/images/")


asyncio.run(main())
