"""Capture the two missing screenshots: NL Intent (Step 4) and Self-Driving Loop (Step 8)."""
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
    close_x = page.locator(".fixed.inset-0.z-50 button").filter(has_text="×")
    if await close_x.count():
        await close_x.click()
        await asyncio.sleep(0.5)
        return
    await page.mouse.click(10, 10)
    await asyncio.sleep(0.5)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        await page.goto(BASE, wait_until="networkidle")
        await asyncio.sleep(2)

        # Set a research topic first
        await page.locator("textarea").first.fill("低 VOC 水性防腐蚀涂料配方研究")
        await asyncio.sleep(0.3)

        # ── 08 NL Intent (Requirements modal) ─────────────────────────────────
        print("08-nl-intent: opening Requirements modal…")
        await page.locator("button:has-text('技术需求')").first.click()
        await asyncio.sleep(1)

        # Fill in the NL intent textarea
        nl_input = page.locator(".fixed.inset-0.z-50 textarea")
        if await nl_input.count():
            await nl_input.first.fill(
                "开发汽车底盘环保水性环氧防腐涂料，耐盐雾 1000 小时，120℃ 固化"
            )
            await asyncio.sleep(0.3)

        # Click the parse button
        parse_btn = page.locator(".fixed.inset-0.z-50 button").filter(has_text="解析")
        if not await parse_btn.count():
            parse_btn = page.locator(".fixed.inset-0.z-50 button").filter(has_text="智能")
        if await parse_btn.count():
            await parse_btn.first.click()
            await asyncio.sleep(2)

        await shot(page, "08-nl-intent.png")
        await close_modal(page)

        # ── 09 Self-Driving Loop modal ─────────────────────────────────────────
        print("09-loop: opening Self-Driving Loop modal…")
        await page.locator("button:has-text('自驱动闭环')").first.click()
        await asyncio.sleep(1)

        # Click the "run loop" button
        run_btn = page.locator(".fixed.inset-0.z-50 button").filter(has_text="迭代")
        if not await run_btn.count():
            run_btn = page.locator(".fixed.inset-0.z-50 button").filter(has_text="闭环")
        if await run_btn.count():
            await run_btn.first.click()
            await asyncio.sleep(8)

        await shot(page, "09-loop.png")
        await close_modal(page)

        await browser.close()

    print("\nExtra screenshots saved to docs/images/")


asyncio.run(main())
