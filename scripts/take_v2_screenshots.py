"""Capture the four modals added with the topology unlock.

10-inverse-design · 11-substitution · 12-qc-report · 13-version-history

Run against a live dev stack:
    cd backend && FORMUMIND_API_AUTH_ENABLED=false uvicorn app.main:app --port 8000
    cd frontend && npm run dev
    python3 scripts/take_v2_screenshots.py

Nothing here is stubbed: the inverse-design run is a real NSGA-II search and the
substitution table a real prediction, so the offline fallbacks are what make the
result deterministic without an LLM key.

**The step order is a dependency order, not a narrative one.** Substitution and
version history need a populated formula leaderboard, and the QC report modal
needs at least one experiment to bind measurements to. Opening them on a cold
app photographs the empty state instead — which is what the first run of this
script produced.

Seeding **appends**: experiment records and version lineages accumulate across
runs, so a second run shows more experiments in the QC picker and may extend an
existing lineage rather than start a fresh one. Point ``FORMUMIND_DB_URL`` at a
throwaway database for a clean capture.
"""
import asyncio
import glob
import os
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

IMAGES = Path(__file__).parent.parent / "docs" / "images"
BASE = "http://localhost:5173"
API = "http://localhost:8000"
VIEWPORT = {"width": 1440, "height": 900}


def chromium_path() -> str | None:
    """A preinstalled Chromium, if this environment ships one.

    Playwright pins an exact browser build per release, so an image that
    preinstalls browsers goes stale the moment the pip package is upgraded.
    Pointing at what is actually on disk avoids re-downloading ~170 MB to take
    four screenshots. None means "use Playwright's own managed browser".
    """
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    for pattern in (f"{root}/chromium-*/chrome-linux/chrome",
                    f"{root}/chromium_headless_shell-*/chrome-linux/headless_shell"):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return None


def seed_experiments() -> None:
    """Give the QC modal experiments to bind a report to.

    Six points so a metric crosses the training threshold, which also makes the
    model gauges real rather than empty.
    """
    import json

    records = [
        {
            "domain": "anticorrosion_coating",
            "label": f"DOE-{i + 1}",
            "factors": {
                "Zinc phosphate": z,
                "Bisphenol-A epoxy (DGEBA)": 38,
                "Polyamide hardener": 14,
            },
            "cure_temperature_c": 80,
            "measured": {"salt_spray_hours": 200 + 80 * z},
        }
        for i, z in enumerate([3, 5, 7, 9, 11, 13])
    ]
    body = json.dumps({"records": records, "retrain": True}).encode()
    request = urllib.request.Request(
        f"{API}/api/experiments", data=body,
        headers={"content-type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        report = json.load(response)
    print(f"  seeded {report.get('total_records')} experiment records")


def _api_json(path: str, payload=None):
    import json

    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{API}{path}", data=data,
        headers={"content-type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def seed_second_revision() -> None:
    """Append a revision that actually differs, so the diff has something to show.

    Reads back the lineage the UI just created rather than inventing one, then
    swaps the hardener — the change the diff summary is meant to describe.
    """
    lineages = _api_json("/api/formulations/versions?limit=1")
    if not lineages:
        print("    ! no lineage to extend")
        return
    lineage = lineages[0]
    versions = lineage.get("versions") or []
    if not versions:
        return
    latest = versions[-1]
    detail = _api_json(f"/api/formulations/versions/detail/{latest['id']}")
    formulation = detail.get("snapshot") or detail.get("formulation")
    if not formulation:
        print("    ! version detail carried no snapshot")
        return

    for ingredient in formulation.get("ingredients", []):
        if "Polyamide hardener" in ingredient.get("name", ""):
            ingredient["name"] = "Isophorone diamine (IPDA)"
            break
    _api_json("/api/formulations/versions", {
        "formulation": formulation,
        "lineage_id": lineage["lineage_id"],
        "parent_version_id": latest["id"],
        "change_summary": "",          # let describe_diff generate it
    })
    print("    seeded a second revision (hardener swap)")


async def shot(page, name: str) -> None:
    await asyncio.sleep(0.9)
    await page.screenshot(path=str(IMAGES / name), full_page=False)
    print(f"  ✓ {name}")


async def open_action(page, title: str) -> None:
    await page.locator(f"button:has-text('{title}')").first.click()
    await asyncio.sleep(1.2)


async def close_modal(page) -> None:
    """Close every open modal.

    Target the header button by its title, not by its "×" glyph: the inverse
    design body uses the same character for its remove-constraint buttons, so a
    text match lands a click inside the dialog. Modals also stack — a formula
    card opens version history over the leaderboard — hence the loop.
    """
    for _ in range(4):
        header_close = page.locator('button[title="关闭 (Esc)"]')
        if not await header_close.count():
            break
        await header_close.last.click()
        await asyncio.sleep(0.6)
    await page.keyboard.press("Escape")
    await asyncio.sleep(0.4)


def modal_button(page, text: str):
    return page.locator(".fixed.inset-0.z-50 button").filter(has_text=text)


async def main() -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    print("seeding backend…")
    seed_experiments()

    async with async_playwright() as p:
        launch_args: dict = {"headless": True, "args": ["--no-sandbox", "--disable-gpu"]}
        if (chrome := chromium_path()):
            launch_args["executable_path"] = chrome
            print(f"using preinstalled chromium: {chrome}")
        browser = await p.chromium.launch(**launch_args)
        ctx = await browser.new_context(viewport=VIEWPORT)
        page = await ctx.new_page()

        await page.goto(BASE, wait_until="networkidle")
        await asyncio.sleep(2.5)
        await page.locator("textarea").first.fill("低 VOC 水性环氧防腐涂料，耐盐雾 1000 小时")
        await asyncio.sleep(0.4)

        # ── 10 · Inverse design ───────────────────────────────────────────────
        # First, because adopting its Pareto front is what populates the
        # leaderboard that steps 11 and 13 depend on.
        print("10-inverse-design…")
        await open_action(page, "逆向设计")
        run = modal_button(page, "开始逆向设计")
        if await run.count():
            await run.first.click()
            await asyncio.sleep(16)          # a real 48 x 30 NSGA-II search
        await shot(page, "10-inverse-design.png")

        adopt = modal_button(page, "采用前沿候选到配方排行")
        if await adopt.count():
            await adopt.first.click()
            await asyncio.sleep(2)
            print("    adopted the frontier into the leaderboard")
        await close_modal(page)

        # ── 11 · Material substitution ────────────────────────────────────────
        print("11-substitution…")
        await open_action(page, "材料替代")
        await asyncio.sleep(1)
        find = modal_button(page, "查找替代")
        if not await find.count():
            find = modal_button(page, "替代")
        if await find.count():
            await find.first.click()
            await asyncio.sleep(8)
        await shot(page, "11-substitution.png")
        await close_modal(page)

        # ── 12 · QC report ────────────────────────────────────────────────────
        print("12-qc-report…")
        await open_action(page, "检测报告")
        await asyncio.sleep(2)
        await shot(page, "12-qc-report.png")
        await close_modal(page)

        # ── 13 · Version history ──────────────────────────────────────────────
        # Opened from a formula card, over whatever modal holds the leaderboard.
        # A single saved revision only proves the modal renders; the feature is
        # the diff, so seed a second revision that genuinely differs and open
        # the comparison.
        print("13-version-history…")
        # The cards carrying the 修订历史 button live in the Recommend
        # leaderboard, not in the inverse-design result table.
        await open_action(page, "推荐配方")
        history = modal_button(page, "修订历史")
        if not await history.count():
            rec = modal_button(page, "推荐")
            if await rec.count():
                await rec.first.click()
                await asyncio.sleep(14)
            history = modal_button(page, "修订历史")
        if not await history.count():
            print("    ! no 修订历史 button found — leaderboard is empty")
            await close_modal(page)
        else:
            await history.first.click()
            await asyncio.sleep(1.5)
            save = modal_button(page, "保存为新版本")
            if await save.count():
                note = page.locator('.fixed.inset-0 input[placeholder*="修改说明"]')
                if await note.count():
                    await note.first.fill("基线配方")
                await save.first.click()
                await asyncio.sleep(2.5)
                # A real second revision: swap the hardener, through the same
                # endpoint the UI uses.
                seed_second_revision()
                await close_modal(page)
                await open_action(page, "推荐配方")
                await asyncio.sleep(1)
                await modal_button(page, "修订历史").first.click()
                await asyncio.sleep(2)
                diff = modal_button(page, "对比")
                if await diff.count():
                    await diff.first.click()
                    await asyncio.sleep(1.5)
            await shot(page, "13-version-history.png")
            await close_modal(page)

        await browser.close()

    print(f"\nSaved to {IMAGES}")


if __name__ == "__main__":
    asyncio.run(main())
