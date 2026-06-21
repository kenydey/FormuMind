"""Inspect the DOM of the running frontend to understand button/input structure."""
import asyncio
from playwright.async_api import async_playwright

BASE = "http://localhost:5173"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        await page.goto(BASE, wait_until="networkidle")
        await asyncio.sleep(2)

        # Get all buttons
        print("=== BUTTONS ===")
        btns = await page.query_selector_all("button")
        for i, b in enumerate(btns):
            txt = (await b.text_content() or "").strip()[:60]
            disabled = await b.get_attribute("disabled")
            cls = (await b.get_attribute("class") or "")[:80]
            print(f"  [{i}] '{txt}' disabled={disabled} class={cls[:50]}")

        # Get all textareas / inputs
        print("\n=== INPUTS ===")
        els = await page.query_selector_all("textarea, input[type='text'], input:not([type])")
        for i, el in enumerate(els):
            ph = await el.get_attribute("placeholder") or ""
            disabled = await el.get_attribute("disabled")
            cls = (await el.get_attribute("class") or "")[:80]
            print(f"  [{i}] placeholder='{ph[:60]}' disabled={disabled}")

        await browser.close()

asyncio.run(main())
