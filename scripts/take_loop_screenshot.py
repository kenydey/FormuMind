"""Capture self-driving loop modal screenshot."""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from _ui import click_if_present, close_modal, in_modal, open_modal  # noqa: E402

IMAGES = Path(__file__).parent.parent / "docs" / "images"
BASE = "http://localhost:5173"


async def shot(page, path: str):
    await asyncio.sleep(1.0)
    await page.screenshot(path=str(IMAGES / path), full_page=False)
    print(f"  ✓ {path}")


async def close_modal(page):
    close_x = page.locator('[data-testid$="-close"]')
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
        await open_modal(page, "loop")

        run_btn = in_modal(page, "loop", "button").filter(has_text="迭代")
        if not await run_btn.count():
            run_btn = in_modal(page, "loop", "button").filter(has_text="闭环")
        if await run_btn.count():
            await run_btn.first.click()
            await asyncio.sleep(8)

        await shot(page, "09-loop.png")
        await close_modal(page)
        await browser.close()

    print("Done.")


asyncio.run(main())
