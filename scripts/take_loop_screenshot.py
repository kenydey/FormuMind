"""Capture self-driving loop modal screenshot."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

IMAGES = Path(__file__).parent.parent / "docs" / "images"
BASE = "http://localhost:5173"


async def shot(page, path: str):
    await asyncio.sleep(1.0)
    await page.screenshot(path=str(IMAGES / path), full_page=False)
    print(f"  ✓ {path}")


async def close_modal(page):
    close_x = page.locator(".fixed.inset-0.z-50 button[title*='关闭']")
    if await close_x.count():
        await close_x.first.click()
    else:
        await page.mouse.click(10, 10)
    await asyncio.sleep(0.5)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        await page.goto(BASE, wait_until="networkidle")
        await asyncio.sleep(2)

        # Open Self-Driving Loop modal
        print("09-loop…")
        await page.locator("button:has-text('自驱动闭环')").first.click()
        await asyncio.sleep(1)

        run_btn = page.locator(".fixed.inset-0.z-50 button").filter(has_text="迭代")
        if not await run_btn.count():
            run_btn = page.locator(".fixed.inset-0.z-50 button").filter(has_text="闭环")
        if await run_btn.count():
            await run_btn.first.click()
            await asyncio.sleep(8)

        await shot(page, "09-loop.png")
        await close_modal(page)
        await browser.close()

    print("Done.")


asyncio.run(main())
