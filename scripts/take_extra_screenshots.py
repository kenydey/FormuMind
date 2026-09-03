"""Capture the two missing screenshots: NL Intent (Step 4) and Self-Driving Loop (Step 8)."""
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
        await open_modal(page, "requirements")

        # Fill in the NL intent textarea
        nl_input = in_modal(page, "requirements", "textarea")
        if await nl_input.count():
            await nl_input.first.fill(
                "开发汽车底盘环保水性环氧防腐涂料，耐盐雾 1000 小时，120℃ 固化"
            )
            await asyncio.sleep(0.3)

        # Click the parse button
        parse_btn = in_modal(page, "requirements", "button").filter(has_text="解析")
        if not await parse_btn.count():
            parse_btn = in_modal(page, "requirements", "button").filter(has_text="智能")
        if not await click_if_present(parse_btn, settle=2):
            print("  ! parse button not found — capturing as-is")

        await shot(page, "08-nl-intent.png")
        await close_modal(page, "requirements")

        # ── 09 Self-Driving Loop modal ─────────────────────────────────────────
        print("09-loop: opening Self-Driving Loop modal…")
        await open_modal(page, "loop")

        # Click the "run loop" button
        run_btn = in_modal(page, "loop", "button").filter(has_text="迭代")
        if not await run_btn.count():
            run_btn = in_modal(page, "loop", "button").filter(has_text="闭环")
        if not await click_if_present(run_btn, settle=8):
            print("  ! loop run button not found — capturing as-is")

        await shot(page, "09-loop.png")
        await close_modal(page)

        await browser.close()

    print("\nExtra screenshots saved to docs/images/")


asyncio.run(main())
